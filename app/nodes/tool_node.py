from __future__ import annotations

import json
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.artifact_content import generate_artifact_content, needs_generated_content
from app.services.fact_layer import attach_turn_facts
from app.services.manuscript_service import WRITING_TOOL_NAMES, resolve_read_paths
from app.services.artifact_tools import (
    collect_file_artifacts,
    extract_math_expression,
    list_task_artifacts,
)
from app.services.metrics_service import get_metrics_service
from app.services.reasoning_trace import report_boundary, report_status_trace
from app.services.state_store import get_state_store
from app.services.tool_dag import execute_tool_stages, parse_tool_stages
from app.services.tool_intent_guard import check_tool_params_safe
from app.services.tool_registry import get_tool_registry
from app.services.turn_event_log import record_turn_event


def tool_execution_node(state: AgentState) -> AgentState:
    """
    Execute registered tools (DAG stages, parallel within stage).

    Reads: selected_tools, input_payload.tool_stages, user_id
    Writes: tool_results, turn_facts, status, current_node, audit_log
    """
    try:
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

        def _invoke(tool_name: str, st: AgentState) -> dict[str, Any]:
            report_status_trace("tool_execution", f"调用工具: {tool_name}")
            params = _build_tool_params(tool_name, st)
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

        results = execute_tool_stages(state, stages, invoke_fn=_invoke)
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
            raise KeyError(failures[0].get("error", "all tools failed"))

        payload_updates = _build_artifact_registry_updates(state, payload, results)
        updated = merge_state(
            state,
            input_payload={**payload, **payload_updates},
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
        get_state_store().save(updated)
        return updated
    except Exception as exc:
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


def _build_tool_params(tool_name: str, state: AgentState) -> dict[str, Any]:
    payload = state.get("input_payload", {})
    goal = str(payload.get("goal") or payload.get("query") or "")
    task_id = state["task_id"]
    tool_params = dict(payload.get("tool_params", {}).get(tool_name, {}))

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
    if tool_name == "edit_text_artifact":
        filename = resolve_read_paths(
            state, str(tool_params.get("filename") or "novel.txt")
        )
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
        filename = _resolve_artifact_filename(
            state,
            tool_name=tool_name,
            requested_filename=str(tool_params.get("filename") or ""),
        )
        params: dict[str, Any] = {
            "task_id": task_id,
            "filename": filename,
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
    return tool_params


def _build_artifact_registry_updates(
    state: AgentState,
    payload: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    produced = collect_file_artifacts(results)
    if not produced:
        return {}
    registry = dict(payload.get("artifact_registry") or {})
    known = dict(registry.get("known") or {})
    for item in produced:
        name = str(item.get("filename") or "").strip()
        path = str(item.get("path") or "").strip()
        if name and path:
            known[name] = path
    last = produced[-1]
    out = dict(registry)
    out["known"] = known
    if str(last.get("filename") or "").strip():
        out["last_written"] = str(last["filename"])
    out["updated_in_node"] = "tool_execution"
    out["updated_turn"] = int(state.get("session_turn") or 0)
    return {"artifact_registry": out}


def _resolve_artifact_filename(
    state: AgentState,
    *,
    tool_name: str,
    requested_filename: str,
) -> str:
    requested = requested_filename.strip()
    if requested:
        return resolve_read_paths(state, requested)

    payload = state.get("input_payload") or {}
    registry = payload.get("artifact_registry") or {}
    known = dict(registry.get("known") or {})

    # 1) deterministic pointer from registry
    last_written = str(registry.get("last_written") or "").strip()
    if last_written:
        return resolve_read_paths(state, last_written)

    # 2) active manuscript pointer (for writing sessions)
    manuscript = payload.get("manuscript") or payload.get("session_artifacts") or {}
    body = str(manuscript.get("body_path") or payload.get("novel_filename") or "").strip()
    if body:
        return resolve_read_paths(state, body)

    # 3) path map from previous writes in this or previous turns
    if known:
        preferred = ("output.md", "novel.txt", "outline.txt")
        for name in preferred:
            if name in known:
                return resolve_read_paths(state, name)
        # deterministic fallback: lexical first key
        first_name = sorted(known.keys())[0]
        return resolve_read_paths(state, first_name)

    # 4) latest tool result path in current runtime state
    latest = collect_file_artifacts(state.get("tool_results"))
    if latest:
        return resolve_read_paths(state, str(latest[-1].get("filename") or "output.md"))

    # 5) scan task artifact directory as a final deterministic fallback
    task_id = str(state["task_id"])
    files = list_task_artifacts(task_id)
    if files:
        preferred = ("output.md", "novel.txt", "outline.txt")
        names = {str(item.get("filename") or "") for item in files}
        for name in preferred:
            if name in names:
                return resolve_read_paths(state, name)
        first_name = sorted(names)[0]
        if first_name:
            return resolve_read_paths(state, first_name)

    mission = state.get("mission") or payload.get("mission") or {}
    if str(mission.get("kind") or "").lower() == "writing" and tool_name == "read_text_artifact":
        policy = mission.get("step_policy") or {}
        outline_name = str(
            policy.get("outline_artifact")
            or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
        )
        task_id = str(state["task_id"])
        files = list_task_artifacts(task_id)
        names = {str(item.get("filename") or "") for item in files}
        if outline_name in names:
            return resolve_read_paths(state, outline_name)
        body_name = str(policy.get("body_artifact") or "novel.txt")
        if body_name in names:
            return resolve_read_paths(state, body_name)

    return resolve_read_paths(state, "output.md")
