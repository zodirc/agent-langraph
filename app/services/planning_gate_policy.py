"""Rule-derived planning gate — single source for needs_planning (no LLM jitter)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState
from app.services.intent_snapshot import current_intent_snapshot
from app.services.writing.revision_command import revision_intent_executable


def mission_active(state: AgentState | dict[str, Any]) -> bool:
    from app.runtime.state_field_access import mission_from_state

    payload = state.get("input_payload") or {}
    return bool(mission_from_state(state)) and not payload.get("mission_suspended")


def derive_planning_required(state: AgentState) -> tuple[bool, str]:
    """Return (planning_required, source_reason) for audit."""
    snap = current_intent_snapshot(state)
    payload = state.get("input_payload") or {}
    intent_obs = state.get("intent_observation") or {}

    if snap and snap.is_revision and snap.revision_intent:
        if revision_intent_executable(snap.revision_intent):
            return False, "rule:revision_executable"
        return True, "rule:revision_needs_scope_resolution"

    is_revision = bool(intent_obs.get("is_revision"))
    revision_intent = intent_obs.get("revision_intent")
    if is_revision and isinstance(revision_intent, dict):
        if revision_intent_executable(revision_intent):
            return False, "rule:revision_executable"
        return True, "rule:revision_needs_scope_resolution"

    from app.services.mission_steer import steer_requires_planning

    if mission_active(state) and not steer_requires_planning(payload):
        return False, "rule:mission_mechanical_step"

    intent_kind = str(intent_obs.get("intent_kind") or payload.get("intent_kind") or "qa")
    session_relation = str(intent_obs.get("session_relation") or "stay")
    if intent_kind == "qa" and session_relation == "stay":
        return False, "rule:qa_stay"

    return True, "rule:default"
