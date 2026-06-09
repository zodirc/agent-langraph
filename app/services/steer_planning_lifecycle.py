"""Deferred steer planning completion (optimization.md §3.1).

Once a steer produces a turn_contract with primary_op, planning settles immediately
(steer_planning_done + steer_contract_pinned). Execute paths still call
maybe_complete_steer_planning_after_execute as a safety net.
"""

from __future__ import annotations

from app.runtime.state import AgentState, merge_state
from app.services.mission_steer import complete_steer_planning, planning_steer_replan_active


def settle_steer_planning_on_contract(payload: dict) -> dict:
    """Pin contract and mark steer planning done when contract has a concrete primary_op."""
    from app.services.turn_contract import contract_from_payload

    contract = contract_from_payload(payload)
    if not contract or not contract.get("primary_op"):
        return payload
    if payload.get("steer_intent_pending_confirm"):
        return payload
    out = complete_steer_planning(payload)
    out["steer_contract_pinned"] = True
    out.pop("steer_planning_complete_pending", None)
    return out


def defer_steer_planning_completion(state: AgentState) -> AgentState:
    """After planning: settle immediately when contract exists, else defer until execute."""
    payload = dict(state.get("input_payload") or {})
    if not planning_steer_replan_active(payload, state):
        return state
    if payload.get("steer_planning_done"):
        return state
    if payload.get("steer_intent_pending_confirm"):
        payload["steer_planning_complete_pending"] = True
        return merge_state(state, input_payload=payload)

    from app.services.turn_contract import contract_from_payload

    if contract_from_payload(payload):
        payload = settle_steer_planning_on_contract(payload)
        return merge_state(state, input_payload=payload)

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
        from app.services.turn_contract import contract_from_payload

        if contract_from_payload(payload):
            payload = settle_steer_planning_on_contract(payload)
            updated = merge_state(state, input_payload=payload)
            from app.services.mission_supersede import settle_foreground_operation

            return settle_foreground_operation(updated)
        return state

    payload = complete_steer_planning(payload)
    payload.pop("steer_planning_complete_pending", None)
    payload["steer_contract_pinned"] = True
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
