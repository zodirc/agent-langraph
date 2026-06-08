"""Deferred steer planning completion (optimization.md §5).

complete_steer_planning runs after execute or gate success, not at planning end.
"""

from __future__ import annotations

from app.runtime.state import AgentState, merge_state
from app.services.mission_steer import complete_steer_planning, planning_steer_replan_active


def defer_steer_planning_completion(state: AgentState) -> AgentState:
    """Mark steer replan planning output ready; completion waits for execute."""
    payload = dict(state.get("input_payload") or {})
    if not planning_steer_replan_active(payload, state):
        return state
    if payload.get("steer_planning_done"):
        return state
    payload["steer_planning_complete_pending"] = True
    return merge_state(state, input_payload=payload)


def maybe_complete_steer_planning_after_execute(state: AgentState) -> AgentState:
    """Complete steer planning gate after tool/writing step or waiting gate."""
    payload = dict(state.get("input_payload") or {})
    if payload.get("steer_planning_done"):
        return state
    pending = payload.get("steer_planning_complete_pending")
    if not pending and not planning_steer_replan_active(payload, state):
        return state
    if not pending:
        return state

    payload = complete_steer_planning(payload)
    payload.pop("steer_planning_complete_pending", None)
    from app.services.mission_supersede import settle_foreground_operation

    updated = merge_state(state, input_payload=payload)
    updated = settle_foreground_operation(updated)
    return updated


def maybe_complete_steer_planning_on_waiting(state: AgentState) -> AgentState:
    """Complete steer planning when entering a confirmation/waiting gate."""
    payload = dict(state.get("input_payload") or {})
    if not payload.get("steer_planning_complete_pending"):
        return state
    if not (
        payload.get("steer_intent_pending_confirm")
        or payload.get("steer_outcome_pending_confirm")
        or state.get("review_required")
    ):
        return state
    return maybe_complete_steer_planning_after_execute(state)
