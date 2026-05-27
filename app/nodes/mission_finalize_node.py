from __future__ import annotations

from app.nodes.reasoning_node import reasoning_node
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.state_store import get_state_store


def mission_finalize_node(state: AgentState) -> AgentState:
    """Ensure reasoning_result exists before policy/output."""
    if not state.get("reasoning_result"):
        state = reasoning_node(state)
    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    metrics = progress.get("metrics") or {}
    summary_extra = ""
    from app.services.mission_orchestrator import orchestration_enabled, orchestration_summary

    if orchestration_enabled(mission):
        summary_extra = f" {orchestration_summary(state)}。"
    elif mission.get("kind") == "writing" and metrics.get("target_chars"):
        summary_extra = (
            f" 进度: {metrics.get('written_chars', 0)}/"
            f"{metrics.get('target_chars')} 字 "
            f"({metrics.get('progress_pct', 0)}%)."
        )
    else:
        summary_extra = ""
    reasoning = dict(state.get("reasoning_result") or {})
    if summary_extra and reasoning.get("summary"):
        reasoning["summary"] = str(reasoning["summary"]) + summary_extra
        state = merge_state(state, reasoning_result=reasoning)

    updated = merge_state(
        state,
        current_node="mission_finalize",
        audit_log=append_audit(state, "mission_finalize", "success", {"kind": mission.get("kind")}),
    )
    get_state_store().save(updated)
    return updated
