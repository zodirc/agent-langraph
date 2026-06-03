"""Mission 收尾节点
无 reasoning_result 时:
  否则 reasoning_node (full LLM)
route_mission_finalize: 无 reasoning 时回 mission_decide。

mission_finalize — exit loop → user-facing reasoning.
  should_skip_llm_reasoning_on_finalize → build_mission_checkpoint_summary
Then: policy → output_guard → output (shared with main graph tail)."""

from __future__ import annotations

from app.nodes.reasoning_node import reasoning_node
from app.runtime.state import AgentState, append_audit, merge_state
from app.services.state_store import get_state_store


def mission_finalize_node(state: AgentState) -> AgentState:
    """Ensure reasoning_result exists before policy/output."""
    from app.services.mission_execution import (
        build_mission_checkpoint_summary,
        should_skip_llm_reasoning_on_finalize,
    )
    from app.services.mission_orchestrator import orchestration_enabled, orchestration_progress_brief

    mission = state.get("mission") or {}
    progress = state.get("progress") or {}
    metrics = progress.get("metrics") or {}

    if not state.get("reasoning_result"):
        if should_skip_llm_reasoning_on_finalize(state):
            checkpoint = build_mission_checkpoint_summary(state)
            state = merge_state(
                state,
                reasoning_result=checkpoint,
                audit_log=append_audit(
                    state,
                    "mission_finalize",
                    "checkpoint_summary",
                    {"source": "mission_checkpoint"},
                ),
            )
        else:
            state = reasoning_node(state)

    summary_extra = ""
    if orchestration_enabled(mission):
        summary_extra = f" {orchestration_progress_brief(state)}"
    elif mission.get("kind") == "writing" and metrics.get("target_chars"):
        summary_extra = (
            f" 进度: {metrics.get('written_chars', 0)}/"
            f"{metrics.get('target_chars')} 字 "
            f"({metrics.get('progress_pct', 0)}%)."
        )

    reasoning = dict(state.get("reasoning_result") or {})
    structured = dict(reasoning.get("structured") or {})
    source = str(structured.get("source") or "")
    if summary_extra and reasoning.get("summary"):
        if source not in ("mission_checkpoint", "mission_writing_skip"):
            reasoning["summary"] = str(reasoning["summary"]) + summary_extra
            state = merge_state(state, reasoning_result=reasoning)

    updated = merge_state(
        state,
        current_node="mission_finalize",
        audit_log=append_audit(state, "mission_finalize", "success", {"kind": mission.get("kind")}),
    )
    get_state_store().save(updated)
    return updated
