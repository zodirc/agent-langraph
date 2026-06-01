"""
Mission act executor — runs pipeline or subgraph steps inside the control loop.
"""

from __future__ import annotations

from app.nodes.planning_node import planning_node
from app.nodes.reasoning_node import reasoning_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.nodes.writing_node import writing_node
from app.services.mission_service import prepare_state_for_mission_act, update_progress_from_observation
from app.services.observation import attach_observation
from app.services.state_store import get_state_store
from app.runtime.router import (
    route_after_planning,
    route_after_retrieval,
    route_after_tool,
    route_after_writing,
)
from app.runtime.state import AgentState, merge_state


def _forced_stop_requested(state: AgentState) -> bool:
    from app.services.mission_steer import pending_has_forced_action

    task_id = str(state.get("task_id") or "")
    if not task_id:
        return False
    stored = get_state_store().load(task_id, read_only=True) or {}
    pending = stored.get("pending_user_message")
    if pending_has_forced_action(pending, "pause"):
        return True
    payload = {
        **(stored.get("input_payload") or {}),
        **(state.get("input_payload") or {}),
    }
    intervention = payload.get("mission_intervention") or {}
    return bool(
        str(intervention.get("action") or "") == "pause" and intervention.get("force")
    )


def run_pipeline_request(state: AgentState) -> AgentState:
    """
    One mission step: planning → retrieval? → tools? → writing? → reasoning.
    Stops before policy/output (mission control handles termination).
    """
    state = prepare_state_for_mission_act(state)
    if _forced_stop_requested(state):
        return merge_state(state, status="MISSION_PAUSED", current_node="mission_act")
    current = planning_node(state)
    from app.services.mission_steer_confirm import (
        attach_steer_confirmation_to_state,
        steer_confirmation_pending,
    )

    if steer_confirmation_pending(current.get("input_payload") or {}):
        return attach_steer_confirmation_to_state(current)

    node = route_after_planning(current)
    safety = 0

    while safety < 12:
        if _forced_stop_requested(current):
            return merge_state(current, status="MISSION_PAUSED", current_node="mission_act")
        safety += 1
        if node == "retrieval":
            current = retrieval_node(current)
            node = route_after_retrieval(current)
        elif node == "tool_execution":
            current = tool_execution_node(current)
            if str(current.get("status", "")).endswith("FAILED"):
                break
            node = route_after_tool(current)
        elif node == "writing":
            current = writing_node(current)
            if str(current.get("status", "")).endswith("FAILED"):
                break
            node = route_after_writing(current)
        elif node == "reasoning":
            current = reasoning_node(current)
            break
        elif node == "planning":
            current = planning_node(current)
            node = route_after_planning(current)
        elif node == "dead_letter":
            break
        else:
            break

    return current


def _mission_writing_reasoning_summary(state: AgentState) -> str:
    """Deterministic post-write summary — narrative outcome when available."""
    from app.runtime.state import TaskStatus, append_audit, merge_state

    obs = state.get("observation") or {}
    manuscript = state.get("manuscript") or obs.get("manuscript") or {}
    metrics = obs.get("progress_metrics") or (state.get("progress") or {}).get("metrics") or {}
    payload = state.get("input_payload") or {}
    outcome = payload.get("last_chapter_outcome") or {}
    path = manuscript.get("body_path") or manuscript.get("outline_path") or "artifact"
    written = metrics.get("written_chars", manuscript.get("body_bytes", 0))
    pct = metrics.get("progress_pct", 0)
    chapter = manuscript.get("chapter_cursor") or metrics.get("chapter_cursor")
    parts = [f"【本轮已执行】继续写作完成：{path}"]
    if written:
        parts.append(f"约 {written} 字")
    if pct:
        parts.append(f"进度 {pct}%")
    if chapter:
        parts.append(f"第 {chapter} 章")
    if outcome.get("chapter_summary"):
        parts.append(f"本章：{str(outcome['chapter_summary'])[:120]}")
    elif outcome.get("ending_state"):
        parts.append(f"章末：{str(outcome['ending_state'])[:80]}")
    quality = metrics.get("chapter_quality") or outcome.get("quality_rubric")
    if quality and quality.get("composite_score") is not None:
        gate = "通过" if quality.get("pass_gate") else "待校正"
        parts.append(f"质量 {quality['composite_score']:.2f}（{gate}）")
    summary = "，".join(parts) + "。"
    reasoning_result = {
        "summary": summary,
        "confidence": 0.92,
        "risk_level": "LOW",
        "structured": {"source": "mission_writing_skip"},
    }
    return merge_state(
        state,
        reasoning_result=reasoning_result,
        status=TaskStatus.REASONED.value,
        current_node="reasoning",
        audit_log=append_audit(
            state,
            "reasoning",
            "success",
            {"confidence": 0.92, "source": "mission_writing_skip"},
        ),
    )


def _run_contract_tool_step(state: AgentState) -> AgentState | None:
    """Execute turn_contract tools without re-entering full planning."""
    from app.services.turn_contract import contract_blocks_writing, contract_tool_names

    payload = state.get("input_payload") or {}
    if not contract_blocks_writing(payload):
        return None
    tools = contract_tool_names(payload)
    if not tools:
        return None
    return tool_execution_node(merge_state(state, selected_tools=tools))


def run_subgraph_writing(state: AgentState) -> AgentState:
    """Writing-focused step: mission step_policy → writing → lightweight reasoning."""
    from app.services.turn_contract import contract_blocks_writing

    state = prepare_state_for_mission_act(state)
    if _forced_stop_requested(state):
        return merge_state(state, status="MISSION_PAUSED", current_node="mission_act")
    payload = dict(state.get("input_payload") or {})
    if contract_blocks_writing(payload):
        tool_step = _run_contract_tool_step(state)
        if tool_step is not None:
            return tool_step
        return run_pipeline_request(state)

    intent = payload.get("writing_intent") or {}
    if not intent.get("enabled"):
        item = payload.get("current_work_item") or {}
        kind = str(item.get("kind") or "")
        write_kinds = frozenset(
            {"append_body", "write_body", "write_outline", "reset_body"}
        )
        if kind not in write_kinds:
            return run_pipeline_request(state)
        mission = state.get("mission") or {}
        from app.services.mission_schema import resolve_writing_intent_for_step

        resolved = resolve_writing_intent_for_step(state, mission=mission)
        if not resolved.get("enabled"):
            return run_pipeline_request(state)
        payload["writing_intent"] = {**resolved, "enabled": True, "source": "work_item"}
        state = merge_state(state, input_payload=payload)

    current = writing_node(state)
    if str(current.get("status", "")).endswith("FAILED"):
        return current

    current = attach_observation(current)
    current = update_progress_from_observation(current)

    payload = current.get("input_payload") or {}
    if payload.get("force_slow_reasoning") or payload.get("revision_intent"):
        return reasoning_node(current)
    if str(current.get("status", "")) == "WRITTEN":
        return _mission_writing_reasoning_summary(current)
    return reasoning_node(current)


def execute_mission_step(state: AgentState, step_decision: dict) -> AgentState:
    """Dispatch act phase by next_executor from control."""
    from app.runtime.state import AgentState, TaskStatus, merge_state
    from app.services.manuscript_service import resolve_manuscript

    from app.services.mission_steer import mission_must_run_planning

    from app.services.mission_steer import review_outline_requested
    from app.services.mission_steer_confirm import steer_confirmation_pending
    from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending

    payload = state.get("input_payload") or {}
    if steer_confirmation_pending(payload) and not payload.get("steer_intent_confirmed"):
        return merge_state(
            state,
            current_node="mission_act",
            status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
        )
    if steer_outcome_confirmation_pending(payload) and not payload.get("steer_outcome_confirmed"):
        from app.services.mission_steer_outcome_confirm import (
            attach_steer_outcome_confirmation_to_state,
        )

        return attach_steer_outcome_confirmation_to_state(
            merge_state(
                state,
                current_node="mission_act",
                status=state.get("status") or TaskStatus.MISSION_RUNNING.value,
            )
        )
    if review_outline_requested(payload) and not mission_must_run_planning(state):
        return run_pipeline_request(state)

    if mission_must_run_planning(state):
        return run_pipeline_request(state)
    item = payload.get("current_work_item") or {}
    kind = str(item.get("kind") or "")

    if kind == "human_gate":
        return merge_state(
            state,
            tool_results=[],
            status=TaskStatus.MISSION_RUNNING.value,
        )

    if kind == "run_tools":
        return run_pipeline_request(state)

    if kind == "edit_plot":
        spec = dict((payload.get("edit_plot_spec") or item.get("params", {}).get("edit_spec") or {}))
        from app.services.outline_steer_patch import is_outline_filename, run_outline_edit_via_tools

        from app.config.settings import settings

        filename = str(
            spec.get("filename")
            or getattr(settings, "MANUSCRIPT_DEFAULT_OUTLINE", "outline.txt")
        )
        if is_outline_filename(filename) and not spec.get("old_text"):
            return run_outline_edit_via_tools(state, spec={**spec, "filename": filename})
        if spec.get("old_text"):
            from app.services.artifact_tools import handle_edit_text_artifact, handle_read_text_artifact

            task_id = state["task_id"]
            filename = str(spec.get("filename") or "novel.txt")
            read_out = handle_read_text_artifact(
                {"task_id": task_id, "filename": filename, "max_chars": 12000}
            )
            edit_out = handle_edit_text_artifact(
                {
                    "task_id": task_id,
                    "filename": filename,
                    "old_text": str(spec.get("old_text")),
                    "new_text": str(spec.get("new_text", "")),
                    "replace_all": bool(spec.get("replace_all", False)),
                    "occurrence_index": spec.get("occurrence_index"),
                    "start_line": spec.get("start_line"),
                    "end_line": spec.get("end_line"),
                    "dry_run": bool(spec.get("dry_run", False)),
                }
            )
            ms = resolve_manuscript(task_id, state.get("manuscript"))
            nbytes = int(edit_out.get("bytes") or 0)
            if is_outline_filename(filename):
                ms.outline_path = filename
                ms.outline_bytes = nbytes
            elif ms.body_path == filename or not ms.body_path:
                ms.body_path = filename
                ms.body_bytes = nbytes
            return merge_state(
                state,
                tool_results=[
                    {"tool": "read_text_artifact", "status": "ok", "result": read_out},
                    {"tool": "edit_text_artifact", "status": "ok", "result": edit_out},
                ],
                manuscript=ms.to_dict(),
                status=TaskStatus.TOOL_EXECUTED.value,
            )
        return run_pipeline_request(state)

    from app.services.turn_contract import contract_blocks_writing

    mission = state.get("mission") or {}
    executor = str(step_decision.get("next_executor") or "pipeline:request")
    if str(mission.get("kind", "")).lower() == "writing" and kind not in (
        "edit_plot",
        "human_gate",
    ):
        executor = "subgraph:writing"
    if executor == "subgraph:writing" and contract_blocks_writing(payload):
        tool_step = _run_contract_tool_step(state)
        if tool_step is not None:
            return tool_step
        return run_pipeline_request(state)
    if executor == "subgraph:writing":
        return run_subgraph_writing(state)
    if executor == "tools_only":
        return tool_execution_node(state)
    return run_pipeline_request(state)
