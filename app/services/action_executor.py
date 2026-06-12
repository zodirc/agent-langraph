"""Action 执行器 —— 统一动作管线的单一执行入口（unified-core WP-1）。

消费 ``state["planned_actions"]``（``Action.to_dict()`` 列表），逐个执行：

- ``read_artifact`` / ``write_artifact`` / ``edit_artifact`` → artifact 处理器直连
  （``Action.as_tool_call`` 已产出后端参数，零翻译鸿沟）；
- ``run_tool`` → 工具注册表 ``invoke``；
- ``run_code`` → 工程执行（``run_engineering_bounded``）；
- ``answer`` / ``retrieve`` → 跳过（由 reasoning / retrieval 节点处理）。

每个结果以 tool_node 兼容的形状 append 到 ``tool_results``；
``edit_artifact`` 用 ``is_edit_applied`` 在 ``turn_facts`` 标注编辑诚实契约
（0 替换 = 未完成），供收敛门（converge）判定。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.domain.action import Action, is_edit_applied
from app.runtime.state import AgentState, append_audit, merge_state

logger = logging.getLogger(__name__)

# Action types executed by this module (the rest are node-handled).
_NODE_HANDLED_TYPES = ("answer", "retrieve")


def has_planned_actions(state: AgentState) -> bool:
    """True when the planner emitted at least one executable Action."""
    actions = state.get("planned_actions") or []
    return any(
        isinstance(a, dict) and str(a.get("type") or "") not in _NODE_HANDLED_TYPES
        for a in actions
    )


def _artifact_handler(action_type: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    from app.services.artifact_tools import (
        handle_edit_text_artifact,
        handle_read_text_artifact,
        handle_write_text_artifact,
    )

    return {
        "read_artifact": handle_read_text_artifact,
        "write_artifact": handle_write_text_artifact,
        "edit_artifact": handle_edit_text_artifact,
    }[action_type]


def _error_entry(
    tool: str,
    exc: Exception,
    *,
    action_type: str,
    error_code: str | None = None,
    non_retryable: bool = False,
) -> dict[str, Any]:
    inner: dict[str, Any] = {"status": "error", "error": str(exc)}
    if error_code:
        inner["error_code"] = error_code
    if non_retryable:
        inner["non_retryable"] = True
    entry: dict[str, Any] = {
        "tool": tool,
        "status": "error",
        "error": str(exc),
        "action_type": action_type,
        "result": inner,
    }
    if error_code:
        entry["error_code"] = error_code
    if non_retryable:
        entry["non_retryable"] = True
    return entry


def _invoke_registry_tool(name: str, params: dict[str, Any], *, user_role: str) -> dict[str, Any]:
    from app.services.tool_invoke_helpers import invoke_tool_with_guards

    return invoke_tool_with_guards(name, params, user_role=user_role)


def _execute_run_tool(action: Action, state: AgentState) -> dict[str, Any]:
    params = dict(action.params)
    name = str(params.pop("name", "") or "")
    if not name:
        return _error_entry(
            "run_tool",
            ValueError("run_tool action requires params.name"),
            action_type="run_tool",
            non_retryable=True,
        )
    payload = state.get("input_payload") or {}
    user_role = str(payload.get("user_role", "user"))
    params.setdefault("task_id", str(state["task_id"]))
    if name in ("write_text_artifact", "append_text_artifact"):
        from app.services.artifact_content import generate_artifact_content, needs_generated_content

        goal = str(
            (state.get("input_payload") or {}).get("goal")
            or (state.get("input_payload") or {}).get("query")
            or ""
        )
        filename = str(params.get("filename") or "")
        raw = params.get("content")
        content = "" if raw is None else str(raw).strip()
        if filename and needs_generated_content(content, goal):
            from app.services.writing_project import body_draft_tool_name

            draft_tool = body_draft_tool_name(str(state["task_id"]), filename)
            params["content"] = generate_artifact_content(
                state=state,
                tool_name=draft_tool,
                filename=filename,
                goal=goal,
            )
            name = draft_tool
    try:
        invoked = _invoke_registry_tool(name, params, user_role=user_role)
    except KeyError as exc:
        return _error_entry(name, exc, action_type="run_tool", error_code="unknown_tool", non_retryable=True)
    except PermissionError as exc:
        return _error_entry(name, exc, action_type="run_tool", error_code="permission_denied", non_retryable=True)
    except Exception as exc:  # tool runtime failure stays a result, not a crash
        return _error_entry(name, exc, action_type="run_tool")
    result_body = invoked.get("result") if isinstance(invoked.get("result"), dict) else {}
    if str(result_body.get("status") or "ok") == "error":
        return _error_entry(
            name,
            ValueError(str(result_body.get("error") or "tool failed")),
            action_type="run_tool",
        )
    return {**invoked, "status": "ok", "action_type": "run_tool"}


def resolve_imprecise_edit_as_write(
    state: AgentState,
    tool_name: str,
    params: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Redirect imprecise writing edits to write_text_artifact + LLM draft."""
    if tool_name != "edit_text_artifact" or _edit_has_target(params):
        return tool_name, params
    payload = state.get("input_payload") or {}
    if not (payload.get("writing_intent") or {}).get("enabled"):
        return tool_name, params
    write_params = dict(params)
    write_params["content"] = _resolve_write_content(state, write_params)
    return "write_text_artifact", write_params


def _edit_has_target(params: dict[str, Any]) -> bool:
    if isinstance(params.get("edits"), list) and params.get("edits"):
        return True
    if params.get("start_line") is not None:
        return True
    return bool(str(params.get("old_text") or "").strip())


def _resolve_write_content(state: AgentState, params: dict[str, Any]) -> str:
    """Fill empty write params via the same LLM draft path as tool_node."""
    from app.services.artifact_content import generate_artifact_content, needs_generated_content

    payload = state.get("input_payload") or {}
    goal = str(
        payload.get("goal")
        or payload.get("query")
        or payload.get("question")
        or ""
    )
    raw = params.get("content")
    content = "" if raw is None else str(raw).strip()
    if not needs_generated_content(content, goal):
        return content

    filename = str(params.get("filename") or "")
    if not filename:
        raise ValueError("write_artifact requires params.filename")

    from app.services.writing_project import body_draft_tool_name

    task_id = str(state["task_id"])
    tool_name = body_draft_tool_name(task_id, filename)
    return generate_artifact_content(
        state=state,
        tool_name=tool_name,
        filename=filename,
        goal=goal,
    )


def _execute_artifact(action: Action, state: AgentState) -> dict[str, Any]:
    call = action.as_tool_call(str(state["task_id"]))
    assert call is not None  # artifact types always map to a registry tool name
    params = dict(call["params"])
    if action.type in ("write_artifact", "edit_artifact"):
        # Run fence: stale/cancelled runs must not persist writes.
        params["_agent_state"] = state
    if action.type == "write_artifact":
        params["content"] = _resolve_write_content(state, params)
        from app.services.writing_project import body_draft_tool_name

        filename = str(params.get("filename") or "")
        draft_tool = body_draft_tool_name(str(state["task_id"]), filename)
        if draft_tool == "append_text_artifact":
            try:
                from app.services.artifact_tools import handle_append_text_artifact

                result = handle_append_text_artifact(params)
            except FileNotFoundError as exc:
                return _error_entry(
                    "append_text_artifact", exc, action_type=action.type,
                    error_code="artifact_not_found", non_retryable=True,
                )
            except ValueError as exc:
                return _error_entry("append_text_artifact", exc, action_type=action.type, non_retryable=True)
            except Exception as exc:
                return _error_entry("append_text_artifact", exc, action_type=action.type)
            result_body = result if isinstance(result, dict) else {"content": str(result)}
            return {
                "tool": "append_text_artifact",
                "status": "ok",
                "action_type": action.type,
                "result": result_body,
            }
    elif action.type == "edit_artifact" and not _edit_has_target(params):
        params["content"] = _resolve_write_content(state, {**params, "filename": params.get("filename")})
        action_type = "write_artifact"
        try:
            result = _artifact_handler("write_artifact")(params)
        except FileNotFoundError as exc:
            return _error_entry(
                call["name"], exc, action_type=action.type,
                error_code="artifact_not_found", non_retryable=True,
            )
        except ValueError as exc:
            return _error_entry(call["name"], exc, action_type=action.type, non_retryable=True)
        except Exception as exc:
            return _error_entry(call["name"], exc, action_type=action.type)
        result_body = result if isinstance(result, dict) else {"content": str(result)}
        return {
            "tool": "write_text_artifact",
            "status": "ok",
            "action_type": action.type,
            "result": {**result_body, "mode": "write_fallback"},
        }
    if action.type == "read_artifact":
        from app.services.artifact_read_guard import block_repeat_artifact_read

        filename = str(params.get("filename") or "")
        blocked = block_repeat_artifact_read(state, filename=filename)
        if blocked is not None:
            status = str(blocked.get("status") or "cached")
            entry: dict[str, Any] = {
                "tool": call["name"],
                "status": status,
                "action_type": action.type,
                "result": blocked,
            }
            if blocked.get("error"):
                entry["error"] = str(blocked["error"])
                entry["error_code"] = blocked.get("error_code")
                entry["non_retryable"] = bool(blocked.get("non_retryable"))
            return entry
    try:
        result = _artifact_handler(action.type)(params)
    except FileNotFoundError as exc:
        return _error_entry(
            call["name"], exc, action_type=action.type,
            error_code="artifact_not_found", non_retryable=True,
        )
    except ValueError as exc:
        return _error_entry(call["name"], exc, action_type=action.type, non_retryable=True)
    except Exception as exc:
        return _error_entry(call["name"], exc, action_type=action.type)
    return {
        "tool": call["name"],
        "status": "ok",
        "action_type": action.type,
        "result": result,
    }


def execute_actions(state: AgentState) -> AgentState:
    """Execute ``planned_actions`` sequentially; append results to ``tool_results``.

    Reads: planned_actions, task_id, input_payload.user_role
    Writes: tool_results, turn_facts(edit_applied/actions_executed), audit_log
    """
    raw_actions = list(state.get("planned_actions") or [])
    if not raw_actions:
        return state

    results: list[dict[str, Any]] = list(state.get("tool_results") or [])
    turn_facts: dict[str, Any] = dict(state.get("turn_facts") or {})
    executed: list[dict[str, Any]] = []
    edits_attempted = 0
    edits_applied = 0
    current = state

    for raw in raw_actions:
        try:
            action = Action.from_dict(raw if isinstance(raw, dict) else {})
        except (ValueError, TypeError) as exc:
            results.append(
                _error_entry("planned_action", exc, action_type="invalid", non_retryable=True)
            )
            executed.append({"type": "invalid", "status": "error"})
            continue

        if action.type in _NODE_HANDLED_TYPES:
            executed.append({"type": action.type, "status": "deferred"})
            continue

        if action.type == "run_code":
            from app.services.engineering_execution import run_engineering_bounded

            current = merge_state(current, tool_results=results, turn_facts=turn_facts or None)
            current = run_engineering_bounded(current)
            results = list(current.get("tool_results") or [])
            turn_facts = dict(current.get("turn_facts") or {})
            executed.append({"type": "run_code", "status": str(current.get("status") or "")})
            continue

        if action.type == "run_tool":
            entry = _execute_run_tool(action, current)
        else:
            entry = _execute_artifact(action, current)
        results.append(entry)
        status = str(entry.get("status") or "ok")
        executed.append({"type": action.type, "status": status, "tool": entry.get("tool")})

        if action.type == "edit_artifact":
            edits_attempted += 1
            if is_edit_applied(entry.get("result") or {}):
                edits_applied += 1

    turn_facts["actions_executed"] = executed
    if edits_attempted:
        turn_facts["edits_attempted"] = edits_attempted
        turn_facts["edits_applied"] = edits_applied
        # Honesty contract: every attempted edit must have actually changed the file.
        turn_facts["edit_applied"] = edits_applied == edits_attempted

    from app.services.artifact_write_honesty import annotate_write_honesty

    turn_facts = annotate_write_honesty(turn_facts, results)

    return merge_state(
        current,
        tool_results=results,
        turn_facts=turn_facts,
        audit_log=append_audit(
            current,
            "action_executor",
            "executed",
            {
                "actions": len(raw_actions),
                "executed": [e for e in executed if e.get("status") != "deferred"],
                "edits_attempted": edits_attempted,
                "edits_applied": edits_applied,
            },
        ),
    )


__all__ = ["execute_actions", "has_planned_actions"]
