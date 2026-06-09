"""工具节点 tool_execution_node：按 tool_dag 执行 selected_tools。

parse_tool_stages → execute_tool_stages；单工具经 tool_intent_guard 与 registry.invoke。
失败处理：全失败 TOOL_FAILED；全非 retryable 时 router 转 reasoning。
Skill 白名单在 planning 阶段已收窄工具列表。

tool_execution_node runs tools via parse_tool_stages and execute_tool_stages.
Uses tool_registry.invoke; route_after_tool selects next node.
"""

from __future__ import annotations

import json
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.artifact_content import generate_artifact_content, needs_generated_content
from app.services.fact_layer import attach_turn_facts
from app.services.artifact_resolver import (
    ArtifactResolutionError,
    action_for_tool,
    maybe_run_outline_edit,
    resolve_artifact_target,
)
from app.services.manuscript_service import WRITING_TOOL_NAMES
from app.services.artifact_tools import extract_math_expression
from app.services.metrics_service import get_metrics_service
from app.services.reasoning_trace import report_boundary, report_status_trace
from app.services.state_store import get_state_store
from app.services.tool_dag import execute_tool_stages, parse_tool_stages
from app.services.tool_intent_guard import check_tool_params_safe
from app.services.tool_registry import get_tool_registry
from app.services.turn_event_log import record_turn_event
from app.services.revision_side_effects import maybe_invalidate_intent_on_tool_result


def tool_execution_node(state: AgentState) -> AgentState:
    """
    Execute registered tools (DAG stages, parallel within stage).

    Reads: selected_tools, input_payload.tool_stages, user_id
    Writes: tool_results, turn_facts, status, current_node, audit_log
    """
    try:
        from app.services.run_controller import RunCancelled, RunController

        try:
            RunController.assert_run_active(state, phase="tool_execution_enter")
        except RunCancelled:
            return merge_state(state, current_node="tool_execution")
        outline_edit = maybe_run_outline_edit(state)
        if outline_edit is not None:
            get_state_store().save(outline_edit)
            return outline_edit
        from app.services.writing.revision_executor import (
            execute_revision_fast_path,
            is_revision_tool_path,
        )

        if is_revision_tool_path(state):
            result = execute_revision_fast_path(state, node_id="tool_execution")
            get_state_store().save(result)
            return result
        from app.services.execution_control import (
            CancelRequested,
            PauseRequested,
            check_for_control_signal,
            handle_control_exception,
        )

        try:
            check_for_control_signal(
                str(state["task_id"]),
                phase="tool_execution_enter",
                raise_on_pause=True,
                raise_on_cancel=True,
            )
        except (PauseRequested, CancelRequested) as exc:
            handled = handle_control_exception(state, exc)
            if handled is not None:
                get_state_store().save(handled)
                return handled

        registry = get_tool_registry()
        tools = [
            t
            for t in (state.get("selected_tools") or [])
            if t not in WRITING_TOOL_NAMES
        ]
        payload = dict(state.get("input_payload") or {})
        user_role = str(payload.get("user_role", "user"))
        goal = str(payload.get("goal") or payload.get("query") or "")
        stages = parse_tool_stages(payload, tools)

        report_boundary("tool_execution", "enter", f"{len(tools)} 工具 / {len(stages)} 阶段")

        blocked_results: list[dict[str, Any]] = []
        pending_events: list[tuple[str, str, dict[str, Any]]] = []

        # Per-tool invoke closure passed to execute_tool_stages (may run in thread pool)
        def _invoke(tool_name: str, st: AgentState) -> dict[str, Any]:
            from app.services.execution_control import (
                CancelRequested,
                PauseRequested,
                check_for_control_signal,
            )

            check_for_control_signal(
                str(st["task_id"]),
                phase="tool_invoke",
                raise_on_pause=True,
                raise_on_cancel=True,
            )
            report_status_trace("tool_execution", f"调用工具: {tool_name}")
            try:
                params = _build_tool_params(tool_name, st)
            except ArtifactResolutionError as exc:
                pending_events.append(
                    (
                        "tool_failed",
                        tool_name,
                        {
                            "status": "error",
                            "error": exc.message,
                            "error_code": exc.code,
                            "manifest": exc.manifest_dicts(),
                        },
                    )
                )
                return {
                    "tool": tool_name,
                    "status": "error",
                    "error": exc.message,
                    "error_code": exc.code,
                    "non_retryable": exc.code != "ambiguous",
                    "result": {
                        "status": "error",
                        "error": exc.message,
                        "error_code": exc.code,
                        "manifest": exc.manifest_dicts(),
                        "non_retryable": exc.code != "ambiguous",
                    },
                }
            spec = registry.get(tool_name)
            safe, issues = check_tool_params_safe(
                tool_name,
                params,
                spec,
                goal,
                user_role=user_role,
            )
            if not safe:
                pending_events.append(
                    (
                        "tool_blocked",
                        tool_name,
                        {"issues": issues, "params_keys": list(params.keys())},
                    )
                )
                blocked_results.append(
                    {
                        "tool": tool_name,
                        "status": "skipped",
                        "error": "; ".join(issues),
                        "result": {"status": "blocked", "issues": issues},
                    }
                )
                return blocked_results[-1]["result"]
            if tool_name == "read_text_artifact":
                from app.services.artifact_read_guard import block_repeat_artifact_read

                filename = str(params.get("filename") or "")
                blocked = block_repeat_artifact_read(st, filename=filename)
                if blocked is not None:
                    status = str(blocked.get("status") or "cached")
                    event_status = "cached" if status == "cached" else "blocked"
                    pending_events.append(
                        (
                            "tool_blocked" if event_status == "blocked" else "tool_invoked",
                            tool_name,
                            {
                                "status": event_status,
                                "read_repeat_blocked": True,
                                "filename": filename,
                            },
                        )
                    )
                    wrapped = {
                        "tool": tool_name,
                        "status": event_status,
                        "result": blocked,
                    }
                    if blocked.get("error"):
                        wrapped["error"] = str(blocked["error"])
                        wrapped["error_code"] = blocked.get("error_code")
                        wrapped["non_retryable"] = bool(blocked.get("non_retryable"))
                    blocked_results.append(wrapped)
                    return blocked
            try:
                result = registry.invoke(tool_name, params, user_role=user_role)
            except FileNotFoundError as exc:
                pending_events.append(
                    (
                        "tool_failed",
                        tool_name,
                        {"status": "error", "error": str(exc), "error_code": "artifact_not_found"},
                    )
                )
                return {
                    "tool": tool_name,
                    "status": "error",
                    "error": str(exc),
                    "error_code": "artifact_not_found",
                    "result": {
                        "status": "error",
                        "error": str(exc),
                        "error_code": "artifact_not_found",
                        "non_retryable": True,
                    },
                }
            pending_events.append(
                (
                    "tool_invoked",
                    tool_name,
                    {"status": "ok", "risk_level": spec.risk_level},
                )
            )
            return result

        results = execute_tool_stages(
            state,
            stages,
            invoke_fn=_invoke,
            execution_version=state.get("execution_version"),
        )
        from app.services.tool_commit_gate import merge_observations_to_tool_results

        results = merge_observations_to_tool_results(state, results)
        if blocked_results:
            results = list(results or []) + blocked_results

        failures = [
            r for r in results
            if r.get("status") in ("error", "skipped") or r.get("error")
        ]
        tool_invocations = [r for r in results if r.get("tool") in tools]
        if tools and failures and len(failures) == len(tool_invocations):
            all_non_retryable = all(
                bool(r.get("non_retryable"))
                or bool((r.get("result") or {}).get("non_retryable"))
                for r in failures
            )
            if all_non_retryable:
                updated = merge_state(
                    state,
                    tool_results=results,
                    errors=list(state.get("errors", []))
                    + [f"tool_execution(non_retryable): {failures[0].get('error', 'all tools failed')}"],
                    status=TaskStatus.TOOL_FAILED.value,
                    current_node="tool_execution",
                    audit_log=append_audit(
                        state,
                        "tool_execution",
                        "non_retryable_error",
                        {
                            "tools": tools,
                            "error": failures[0].get("error", "all tools failed"),
                            "error_code": failures[0].get("error_code"),
                        },
                    ),
                )
                get_state_store().save(updated)
                return updated
            msg = str(failures[0].get("error") or "all tools failed")
            updated = merge_state(
                state,
                tool_results=results,
                errors=list(state.get("errors", [])) + [f"tool_execution: {msg}"],
                status=TaskStatus.TOOL_FAILED.value,
                current_node="tool_execution",
                audit_log=append_audit(
                    state,
                    "tool_execution",
                    "all_tools_failed",
                    {"tools": tools, "error": msg},
                ),
            )
            get_state_store().save(updated)
            return updated

        updated = merge_state(
            state,
            tool_results=results,
            status=TaskStatus.TOOL_EXECUTED.value,
            current_node="tool_execution",
            audit_log=append_audit(
                state,
                "tool_execution",
                "success",
                {
                    "tools_run": len(results),
                    "tool_names": tools,
                    "stages": len(stages),
                    "blocked": len(blocked_results),
                },
            ),
        )
        from app.services.context_registry import persist_tool_context

        updated = persist_tool_context(updated, results)
        for item in results:
            updated = maybe_invalidate_intent_on_tool_result(
                updated,
                str(item.get("tool") or ""),
                item,
            )
        for event_type, subject, detail in pending_events:
            updated = record_turn_event(
                updated,
                event_type,
                subject,
                "tool_execution",
                detail,
            )
            if event_type == "tool_invoked":
                from app.services.engineering_trace import record_tool_span

                updated = record_tool_span(updated, subject, status="ok")
            elif event_type == "tool_blocked":
                from app.services.engineering_trace import record_tool_span

                updated = record_tool_span(
                    updated,
                    subject,
                    status="error",
                    error="; ".join(detail.get("issues") or []),
                )
        updated = attach_turn_facts(updated)
        from app.services.steer_planning_lifecycle import maybe_complete_steer_planning_after_execute
        from app.services.turn_guard import mark_turn_step_executed

        updated = mark_turn_step_executed(updated)
        updated = maybe_complete_steer_planning_after_execute(updated)
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        from app.services.execution_control import CancelRequested, PauseRequested, handle_control_exception

        handled = handle_control_exception(state, exc)
        if handled is not None:
            get_state_store().save(handled)
            return handled
        get_metrics_service().inc_tool_failure()
        retry = state.get("retry_count", 0) + 1
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"tool_execution: {exc}"],
            retry_count=retry,
            status=TaskStatus.TOOL_FAILED.value,
            current_node="tool_execution",
            audit_log=append_audit(state, "tool_execution", "error", {"detail": str(exc)}),
        )


def _tool_cfg(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
    raw = payload.get("tool_params")
    if not isinstance(raw, dict):
        return {}
    entry = raw.get(tool_name)
    return dict(entry) if isinstance(entry, dict) else {}


def _revision_tool_params(
    tool_name: str,
    state: AgentState,
    task_id: str,
    tool_params: dict[str, Any],
) -> dict[str, Any] | None:
    payload = state.get("input_payload") or {}
    if payload.get("thin_execution_profile") != "revision_scoped":
        return None
    rev_raw = payload.get("revision_intent")
    if not isinstance(rev_raw, dict) or not rev_raw:
        return None
    from app.services.writing.revision_command import (
        revision_intent_to_edit_params,
        revision_intent_to_read_params,
    )

    if tool_name == "read_text_artifact":
        params = revision_intent_to_read_params(rev_raw, task_id)
        params.update({k: v for k, v in tool_params.items() if k not in params})
        return params
    if tool_name == "edit_text_artifact":
        params = revision_intent_to_edit_params(
            rev_raw,
            task_id,
            dry_run=bool(tool_params.get("dry_run", False)),
        )
        params.update({k: v for k, v in tool_params.items() if k not in params})
        return params
    return None


def _artifact_filename(
    state: AgentState,
    *,
    tool_name: str,
    tool_params: dict[str, Any],
    require_exists: bool = True,
) -> str:
    action = action_for_tool(tool_name, state)
    hint = ""
    payload = state.get("input_payload") or {}
    record = payload.get("writing_intent_record") or {}
    if isinstance(record, dict):
        anchor = record.get("anchor") or {}
        if isinstance(anchor, dict):
            hint = str(anchor.get("target_hint") or "")
    target = resolve_artifact_target(
        state,
        action=action,
        requested_filename=str(tool_params.get("filename") or ""),
        target_hint=hint,
        require_exists=require_exists,
    )
    return target.filename


def _build_tool_params(tool_name: str, state: AgentState) -> dict[str, Any]:
    payload = state.get("input_payload", {})
    goal = str(payload.get("goal") or payload.get("query") or "")
    task_id = state["task_id"]
    tool_params = _tool_cfg(payload, tool_name)

    if tool_name == "echo":
        return {"message": goal or json.dumps(payload)}
    if tool_name == "summarize_text":
        return {"text": goal or json.dumps(payload.get("context", {}))}
    if tool_name == "get_runtime_info":
        return {"task_id": task_id, **tool_params}
    if tool_name == "calculator":
        expression = tool_params.get("expression") or extract_math_expression(goal)
        if not expression:
            raise ValueError("calculator requires expression in tool_params or a math goal")
        return {"expression": str(expression)}
    if tool_name in ("enqueue_mission_work_item", "set_mission_work_plan"):
        return {"task_id": task_id, **tool_params}
    if tool_name in (
        "ls_path",
        "read_file",
        "write_file",
        "append_file",
        "move_path",
        "copy_path",
        "grep_file",
        "replace_in_file",
        "touch_file",
        "mkdir_path",
        "rm_path",
    ):
        return {"task_id": task_id, **tool_params}
    if tool_name == "edit_text_artifact":
        rev_params = _revision_tool_params(tool_name, state, task_id, tool_params)
        if rev_params is not None:
            return rev_params
        filename = _artifact_filename(state, tool_name=tool_name, tool_params=tool_params)
        return {
            "task_id": task_id,
            "filename": filename,
            "old_text": str(tool_params.get("old_text", "")),
            "new_text": str(tool_params.get("new_text", "")),
            "replace_all": bool(tool_params.get("replace_all", False)),
            "occurrence_index": tool_params.get("occurrence_index"),
            "start_line": tool_params.get("start_line"),
            "end_line": tool_params.get("end_line"),
            "dry_run": bool(tool_params.get("dry_run", False)),
            **{
                k: v
                for k, v in tool_params.items()
                if k
                not in (
                    "filename",
                    "old_text",
                    "new_text",
                    "replace_all",
                    "occurrence_index",
                    "start_line",
                    "end_line",
                    "dry_run",
                )
            },
        }
    if tool_name in ("write_text_artifact", "append_text_artifact", "read_text_artifact"):
        rev_params = _revision_tool_params(tool_name, state, task_id, tool_params)
        if rev_params is not None:
            return {**rev_params, "_agent_state": state}
        require_exists = tool_name == "read_text_artifact" or tool_name == "edit_text_artifact"
        if tool_name in ("write_text_artifact", "append_text_artifact"):
            require_exists = False
        filename = _artifact_filename(
            state,
            tool_name=tool_name,
            tool_params=tool_params,
            require_exists=require_exists,
        )
        params: dict[str, Any] = {
            "task_id": task_id,
            "filename": filename,
            "_agent_state": state,
            **tool_params,
        }
        if tool_name != "read_text_artifact":
            from app.services.answer_compose import normalize_code_content

            payload = state.get("input_payload") or {}
            profile = str(
                payload.get("artifact_profile")
                or (payload.get("route_audit") or {}).get("artifact_profile")
                or ""
            ).lower()
            raw = tool_params.get("content")
            if raw is None:
                content = ""
            elif profile == "source_code":
                content = normalize_code_content(str(raw))
            else:
                content = str(raw).strip()
            if needs_generated_content(content, goal):
                content = generate_artifact_content(
                    state=state,
                    tool_name=tool_name,
                    filename=filename,
                    goal=goal,
                )
            params["content"] = content
        return params
    if tool_name == "verify_backend":
        audit = payload.get("route_audit") or {}
        return {
            "task_id": task_id,
            "intent_kind": str(
                payload.get("intent_kind") or audit.get("inferred_kind") or "code"
            ),
            "goal": goal,
            **tool_params,
        }
    return {"task_id": task_id, **tool_params}
