"""Rule-derived planning gate — single source for needs_planning (no LLM jitter)."""

from __future__ import annotations

from app.runtime.state import AgentState


def derive_planning_required(state: AgentState) -> tuple[bool, str]:
    """Return (planning_required, source_reason) for audit."""
    payload = state.get("input_payload") or {}
    intent_obs = state.get("intent_observation") or {}

    intent_kind = str(intent_obs.get("intent_kind") or payload.get("intent_kind") or "qa")
    session_relation = str(intent_obs.get("session_relation") or "stay")
    if intent_kind == "qa" and session_relation == "stay":
        return False, "rule:qa_stay"

    return True, "rule:default"
