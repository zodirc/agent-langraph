"""Apply mode contract to runtime state after planning / route audit."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, append_audit, merge_state
from app.services.delivery_policy import load_delivery_config, resolve_delivery_plan
from app.services.mode_registry import get_mode_contract
from app.services.mode_router import (
    ModeResolution,
    apply_engineering_turn_isolation,
    is_engineering_mode,
    resolve_target_mode,
)
from app.services.mode_execution import apply_manuscript_mode_contract, apply_qa_mode_contract


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _audit_for_delivery(state: AgentState, resolution: ModeResolution) -> dict[str, Any]:
    payload = state.get("input_payload") or {}
    audit = dict(payload.get("route_audit") or {})
    audit["inferred_kind"] = resolution.intent_kind
    audit["kind_confidence"] = resolution.confidence
    return audit


def apply_mode_contract_to_state(
    state: AgentState,
    resolution: ModeResolution,
) -> AgentState:
    """Enforce tools, delivery, guards, and writing/mission blocks from contract."""
    contract = resolution.contract or get_mode_contract(resolution.target_mode)
    if contract is None:
        return state

    payload = dict(state.get("input_payload") or {})
    audit = _audit_for_delivery(state, resolution)
    payload["intent_kind"] = resolution.intent_kind
    payload["target_mode"] = resolution.target_mode
    payload["current_mode"] = resolution.target_mode
    payload["mode_switch_action"] = resolution.mode_switch_action
    payload["mode_switch_reason"] = resolution.mode_switch_reason
    payload["mode_resolution"] = resolution.to_dict()
    payload["execution_path"] = contract.execution.path
    payload["effective_mode_contract"] = resolution.to_dict().get("effective_mode_contract")

    delivery = resolve_delivery_plan(state, audit)
    delivery["primary"] = contract.delivery.primary
    if contract.delivery.secondary:
        delivery["secondary"] = contract.delivery.secondary
    payload["delivery_plan"] = delivery

    tools = list(state.get("selected_tools") or [])
    intent = dict(_coerce_dict(payload.get("writing_intent")))

    if contract.guards.forbid_routes:
        for route in contract.guards.forbid_routes:
            if route.startswith("writing") or route == "mission_writing":
                intent = {
                    **intent,
                    "enabled": False,
                    "blocked_by": "mode_contract",
                    "blocked_route": route,
                }
        payload["writing_intent"] = intent
        audit["writing_blocked"] = True

    if resolution.target_mode == "engineering_mode":
        intent = {**intent, "enabled": False, "blocked_by": "engineering_mode"}
        payload["writing_intent"] = intent
        audit["writing_blocked"] = True
        audit["artifact_profile"] = "engineering_project"
        payload["artifact_profile"] = "engineering_project"
        tools = [t for t in contract.allowed_tools if t]
        payload.pop("mission", None)
        state = merge_state(state, mission=None, execution_mode="single")
        payload["disable_mission_auto"] = True
        payload["skip_retrieval"] = True

    elif resolution.target_mode == "qa_mode":
        payload, audit, tools, state = apply_qa_mode_contract(
            state,
            payload,
            audit,
            tools,
            intent,
            allowed=contract.allowed_tools,
        )

    elif resolution.target_mode == "manuscript_mode":
        payload, audit, tools, state = apply_manuscript_mode_contract(
            state,
            payload,
            audit,
            tools,
            intent,
            allowed=contract.allowed_tools,
        )

    payload["route_audit"] = audit
    state = merge_state(
        state,
        input_payload=payload,
        selected_tools=tools,
        skip_retrieval=payload.get("skip_retrieval", state.get("skip_retrieval")),
        audit_log=append_audit(
            state,
            "mode_resolution",
            resolution.mode_switch_action,
            resolution.to_dict(),
        ),
    )
    return state


def apply_mode_isolation_if_needed(
    state: AgentState,
    resolution: ModeResolution,
) -> AgentState:
    if resolution.mode_switch_action != "isolate":
        return state
    if not is_engineering_mode(resolution):
        return state
    payload = dict(state.get("input_payload") or {})
    existing_payload = state.get("input_payload") or {}
    if state.get("mission") or existing_payload.get("mission"):
        payload = apply_engineering_turn_isolation(payload, {"mission": state.get("mission")})
        state = merge_state(
            state,
            input_payload=payload,
            mission=None,
            execution_mode="single",
        )
    return state


def run_mode_resolution_pipeline(state: AgentState) -> AgentState:
    from app.services.intent_observation import resolve_mode_with_observation

    intent_obs = state.get("intent_observation") if resolve_mode_with_observation(state) else None
    resolution = resolve_target_mode(state, intent_observation=intent_obs)
    state = apply_mode_isolation_if_needed(state, resolution)
    state = apply_mode_contract_to_state(state, resolution)
    from app.services.reasoning_trace import report_mode_resolution_trace

    report_mode_resolution_trace(state)
    from app.services.turn_event_log import record_turn_event

    state = record_turn_event(
        state,
        "mode_resolved",
        "planning",
        "mode_resolution",
        resolution.to_dict(),
    )
    return state


def should_route_engineering_execution(state: AgentState) -> bool:
    payload = state.get("input_payload") or {}
    if str(payload.get("target_mode") or "") != "engineering_mode":
        return False
    from app.services.interaction_goal import goal_is_conversational_qa

    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    return not goal_is_conversational_qa(goal)
