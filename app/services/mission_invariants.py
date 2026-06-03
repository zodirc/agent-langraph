"""Mission output invariants — mechanical checks before user-facing COMPLETED."""

from __future__ import annotations

from app.runtime.state import AgentState
from app.services.turn_kind import agenda_has_executor_pending


def mission_output_must_pause(state: AgentState) -> bool:
    """
    Orchestrated missions must not surface COMPLETED while executor queue pending
    or contract unfulfilled.
    """
    mission = state.get("mission") or {}
    if not mission:
        return False

    from app.services.mission_orchestrator import orchestration_enabled, work_plan_completed

    if orchestration_enabled(mission) and not work_plan_completed(state):
        if agenda_has_executor_pending(state):
            return True

    from app.services.turn_contract import (
        contract_requires_side_effects,
        is_turn_contract_fulfilled,
    )

    payload = state.get("input_payload") or {}
    if contract_requires_side_effects(payload, state=state) and not is_turn_contract_fulfilled(
        state
    ):
        return True

    from app.services.mission_steer_confirm import steer_confirmation_pending

    if steer_confirmation_pending(payload):
        return True

    return False
