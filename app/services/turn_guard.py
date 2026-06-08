"""Turn atomicity guards (optimization.md §5 / Phase C)."""

from __future__ import annotations

from app.runtime.state import AgentState
from app.services.session_fsm import FSM_REPLANNING, FSM_WAITING_USER, get_fsm_state
from app.services.turn_contract import (
    contract_from_payload,
    contract_requires_side_effects,
    is_turn_contract_fulfilled,
)


def turn_executed_step(state: AgentState) -> bool:
    """True when this turn recorded at least one tool or writing execution."""
    payload = state.get("input_payload") or {}
    if payload.get("turn_step_executed"):
        return True
    from app.services.fact_layer import build_turn_facts

    facts = build_turn_facts(state) or {}
    if facts.get("tools_executed"):
        return True
    if facts.get("executed_actions"):
        return True
    return False


def turn_in_waiting(state: AgentState) -> bool:
    fsm = get_fsm_state(state)
    if fsm == FSM_WAITING_USER:
        return True
    if bool(state.get("review_required")):
        return True
    payload = state.get("input_payload") or {}
    if payload.get("steer_intent_pending_confirm") or payload.get("steer_outcome_pending_confirm"):
        return True
    return False


def can_finalize_turn(state: AgentState) -> tuple[bool, str]:
    """
    Return (allowed, reason).

    FORBIDDEN:
      - fsm=REPLANNING emit done/COMPLETED
      - contract requires side effects but none executed and not waiting
    """
    fsm = get_fsm_state(state)
    if fsm == FSM_REPLANNING:
        return False, "fsm_replanning"

    payload = state.get("input_payload") or {}
    if contract_requires_side_effects(payload, state=state):
        if turn_in_waiting(state):
            return True, "waiting_user"
        contract = contract_from_payload(payload) or {}
        primary = str(contract.get("primary_op") or "")
        if primary.startswith("write_") or primary in ("edit_plot", "append_body", "reset_body"):
            if not turn_executed_step(state):
                return False, f"unexecuted_contract:{primary}"
        if turn_executed_step(state):
            return True, "executed"
        if is_turn_contract_fulfilled(state):
            return True, "executed"
        return False, "unexecuted_contract"
    return True, "ok"


def mark_turn_step_executed(state: AgentState) -> AgentState:
    from app.runtime.state import merge_state

    payload = dict(state.get("input_payload") or {})
    payload["turn_step_executed"] = True
    return merge_state(state, input_payload=payload)


def contract_requires_execution_route(payload: dict) -> bool:
    """True when planning gate MUST route to tool/writing, not reasoning shortcut."""
    from app.services.turn_contract import contract_tool_names

    contract = contract_from_payload(payload)
    if not contract:
        return False
    primary = str(contract.get("primary_op") or "")
    if primary in ("edit_plot", "write_outline", "reset_body", "append_body", "write_body"):
        return True
    if contract_tool_names(payload):
        return True
    if contract_requires_side_effects(payload):
        return True
    return False
