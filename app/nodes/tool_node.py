from __future__ import annotations

import json
from typing import Any

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.artifact_content import generate_artifact_content, needs_generated_content
from app.services.fact_layer import attach_turn_facts
from app.services.manuscript_service import WRITING_TOOL_NAMES, resolve_read_paths
from app.services.artifact_tools import extract_math_expression
from app.services.metrics_service import get_metrics_service
from app.services.reasoning_trace import report_boundary, report_status_trace
from app.services.state_store import get_state_store
from app.services.tool_dag import execute_tool_stages, parse_tool_stages
from app.services.tool_registry import get_tool_registry


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
        stages = parse_tool_stages(payload, tools)

        report_boundary("tool_execution", "enter", f"{len(tools)} 工具 / {len(stages)} 阶段")

        def _invoke(tool_name: str, st: AgentState) -> dict[str, Any]:
            report_status_trace("tool_execution", f"调用工具: {tool_name}")
            params = _build_tool_params(tool_name, st)
            return registry.invoke(tool_name, params, user_role=user_role)

        results = execute_tool_stages(state, stages, invoke_fn=_invoke)

        failures = [
            r for r in results
            if r.get("status") in ("error", "skipped") or r.get("error")
        ]
        if tools and failures and len(failures) == len([r for r in results if r.get("tool") in tools]):
            raise KeyError(failures[0].get("error", "all tools failed"))

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
                },
            ),
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
        filename = resolve_read_paths(
            state, str(tool_params.get("filename") or "output.md")
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
