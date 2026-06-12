"""工具节点 tool_execution_node：统一 Action 执行 + 过渡期 staged 执行。

unified-core WP-5：``planned_actions`` 非空时走 ``action_executor.execute_actions``
（规划期已产出全量后端参数，零翻译）；否则回退既有 selected_tools / tool_stages
staged 执行（过渡期保留）。

失败处理：全失败 TOOL_FAILED；全非 retryable 时 router 转 reasoning。
``edit_artifact`` 的编辑诚实（is_edit_applied）由 action_executor 写入 turn_facts。
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
    resolve_artifact_target,
)
from app.services.artifact_tools import extract_math_expression
from app.services.metrics_service import get_metrics_service
from app.services.reasoning_trace import report_boundary, report_status_trace
from app.services.state_store import get_state_store
from app.services.tool_dag import execute_tool_stages, parse_tool_stages
from app.services.tool_intent_guard import check_tool_params_safe
from app.services.tool_registry import get_tool_registry
from app.services.turn_event_log import record_turn_event


def _classify_failures(new_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Return (failures, all_non_retryable) over this turn's new results."""
    failures = [
        r
        for r in new_results
        if r.get("status") in ("error", "skipped") or r.get("error")
    ]
    from app.services.tool_result_helpers import tool_result_flag

    all_non_retryable = bool(failures) and all(
        bool(r.get("non_retryable")) or bool(tool_result_flag(r, "non_retryable"))
        for r in failures
    )
    return failures, all_non_retryable


def _run_planned_actions(state: AgentState) -> AgentState:
    """Unified action pipeline: execute planned_actions and set turn status."""
    from app.services.action_executor import execute_actions
    from app.services.turn_guard import mark_turn_step_executed

    prior_len = len(state.get("tool_results") or [])
    actions = state.get("planned_actions") or []
    report_boundary("tool_execution", "enter", f"{len(actions)} actions")

    updated = execute_actions(state)
    results = list(updated.get("tool_results") or [])
    new_results = results[prior_len:]
    failures, all_non_retryable = _classify_failures(new_results)

    if new_results and len(failures) == len(new_results):
        msg = str(failures[0].get("error") or "all actions failed")
        prefix = "tool_execution(non_retryable)" if all_non_retryable else "tool_execution"
        updated = merge_state(
            updated,
            errors=list(updated.get("errors", [])) + [f"{prefix}: {msg}"],
            status=TaskStatus.TOOL_FAILED.value,
            current_node="tool_execution",
            audit_log=append_audit(
                updated,
                "tool_execution",
                "non_retryable_error" if all_non_retryable else "all_tools_failed",
                {"actions": [str(a.get("type")) for a in actions if isinstance(a, dict)], "error": msg},
            ),
        )
        get_state_store().save(updated)
        return updated

    updated = merge_state(
        updated,
        status=TaskStatus.TOOL_EXECUTED.value,
        current_node="tool_execution",
        audit_log=append_audit(
            updated,
            "tool_execution",
            "success",
            {
                "executed": len(new_results),
                "tool_names": [str(r.get("tool") or "") for r in new_results],
                "failed": len(failures),
                "source": "planned_actions",
            },
        ),
    )
    from app.services.context_registry import persist_tool_context

    updated = persist_tool_context(updated, new_results)
    for item in new_results:
        event = "tool_failed" if item.get("status") == "error" else "tool_invoked"
        updated = record_turn_event(
            updated,
            event,
            str(item.get("tool") or "action"),
            "tool_execution",
            {"status": str(item.get("status") or "ok"), "action_type": item.get("action_type")},
        )
    # attach_turn_facts rebuilds the snapshot; executor honesty facts
    # (edit_applied / edits_* / actions_executed) must survive the rebuild.
    executor_facts = {
        k: v
        for k, v in (updated.get("turn_facts") or {}).items()
        if k in ("edit_applied", "edits_attempted", "edits_applied", "actions_executed")
    }
    updated = attach_turn_facts(updated)
    if executor_facts:
        merged_facts = {**(updated.get("turn_facts") or {}), **executor_facts}
        payload_facts = dict(updated.get("input_payload") or {})
        payload_facts["turn_facts"] = merged_facts
        updated = merge_state(updated, turn_facts=merged_facts, input_payload=payload_facts)
    updated = mark_turn_step_executed(updated)
    get_state_store().save(updated)
    return updated


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

        # Unified pipeline: planner emitted executable actions → action_executor.
        from app.services.action_executor import has_planned_actions

        if has_planned_actions(state):
            return _run_planned_actions(state)

        registry = get_tool_registry()
        tools = [str(t) for t in (state.get("selected_tools") or [])]
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
                from app.services.action_executor import resolve_imprecise_edit_as_write

                tool_name, params = resolve_imprecise_edit_as_write(st, tool_name, params)
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
                from app.services.tool_invoke_helpers import invoke_tool_with_guards

                result = invoke_tool_with_guards(tool_name, params, user_role=user_role)
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
            event_detail: dict[str, Any] = {
                "status": "ok",
                "risk_level": spec.risk_level,
            }
            if tool_name in ("write_text_artifact", "append_text_artifact", "edit_text_artifact"):
                from app.services.writing_context import applied_writing_guideline_ids

                guideline_ids = applied_writing_guideline_ids(st)
                if guideline_ids:
                    event_detail["applied_guidelines"] = guideline_ids
                    if isinstance(result, dict):
                        inner = result.get("result")
                        if isinstance(inner, dict):
                            inner["applied_guidelines"] = guideline_ids
                        else:
                            result["applied_guidelines"] = guideline_ids
            pending_events.append(("tool_invoked", tool_name, event_detail))
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
            from app.services.tool_result_helpers import tool_result_flag

            all_non_retryable = all(
                bool(r.get("non_retryable"))
                or bool(tool_result_flag(r, "non_retryable"))
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
        from app.services.turn_guard import mark_turn_step_executed

        updated = mark_turn_step_executed(updated)
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


def _artifact_filename(
    state: AgentState,
    *,
    tool_name: str,
    tool_params: dict[str, Any],
    require_exists: bool = True,
) -> str:
    target = resolve_artifact_target(
        state,
        action=action_for_tool(tool_name, state),
        requested_filename=str(tool_params.get("filename") or ""),
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
        writing_operator = str(payload.get("writing_operator") or "")
        if writing_operator:
            params["writing_operator"] = writing_operator
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
