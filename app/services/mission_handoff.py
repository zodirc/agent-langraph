"""Single entry for main-graph → mission-graph handoff (debug.log P0)."""

from __future__ import annotations

from typing import Any, Optional

from app.runtime.state import AgentState, TaskStatus, merge_state
from app.services.mission_schema import apply_mission_step_to_payload, should_use_mission_runtime
from app.services.mission_service import init_mission_state


def mission_handoff_needed(
    state: AgentState | dict[str, Any],
    payload: Optional[dict[str, Any]] = None,
    *,
    execution_mode: str = "",
) -> bool:
    """True when planning should hand off to mission_graph instead of ending idle."""
    from app.services.mode_execution import should_route_mission_writing_mode

    payload = dict(payload or (state.get("input_payload") if isinstance(state, dict) else {}) or {})
    mode = str(
        execution_mode
        or (state.get("execution_mode") if isinstance(state, dict) else "")
        or payload.get("execution_mode")
        or ""
    )
    if should_route_mission_writing_mode(state):  # type: ignore[arg-type]
        return True
    return should_use_mission_runtime(payload, mode)


def _handoff_signature(payload: dict[str, Any], state: AgentState) -> str:
    contract = payload.get("turn_contract") or {}
    primary = str(contract.get("primary_op") or "")
    steer = str(payload.get("latest_steer_message") or "").strip()[:80]
    rev = int(payload.get("intent_revision") or 0)
    goal = str(payload.get("goal") or "")[:80]
    turn = int(state.get("session_turn") or 0)
    if steer:
        return f"t{turn}|r{rev}|{primary}|steer:{steer}"
    return f"t{turn}|r{rev}|{primary}|{goal}"


def detect_handoff_planning_loop(payload: dict[str, Any], state: AgentState) -> bool:
    """Same turn + same contract replanned repeatedly → handoff stuck."""
    sig = _handoff_signature(payload, state)
    prev_sig = str(payload.get("_last_planning_contract_sig") or "")
    enter_count = int(payload.get("_planning_enter_count") or 0)
    return enter_count >= 2 and bool(prev_sig) and sig == prev_sig and bool(state.get("mission"))


def record_planning_handoff_attempt(payload: dict[str, Any], state: AgentState) -> dict[str, Any]:
    out = dict(payload)
    out["_planning_enter_count"] = int(out.get("_planning_enter_count") or 0) + 1
    out["_last_planning_contract_sig"] = _handoff_signature(out, state)
    return out


def complete_mission_handoff(
    state: AgentState,
    payload: Optional[dict[str, Any]] = None,
    *,
    source: str = "planning_handoff",
) -> AgentState:
    """
    Idempotent handoff: attach mission runtime without resetting an active mission.

    First handoff: init_mission_state. Re-handoff: preserve mission_step/progress.
    """
    payload = dict(payload or state.get("input_payload") or {})
    if payload.get("mission_handoff_completed") and state.get("mission"):
        return merge_state(
            state,
            input_payload=payload,
            status=TaskStatus.MISSION_RUNNING.value,
            execution_mode=state.get("execution_mode") or payload.get("execution_mode"),
        )

    if state.get("mission"):
        mission = dict(state.get("mission") or {})
        payload = {**payload, "mission": mission}
        from app.services.mission_execution import has_execution_grant

        from app.services.turn_contract import (
            contract_blocks_writing,
            contract_from_payload,
            materialize_writing_intent_from_contract,
        )

        steer_replan_ready = bool(payload.get("steer_planning_done")) and contract_from_payload(
            payload
        )
        if (
            has_execution_grant(payload)
            or not (payload.get("writing_intent") or {}).get("enabled")
            or steer_replan_ready
        ):
            if contract_blocks_writing(payload) or steer_replan_ready:
                contract = contract_from_payload(payload)
                if contract:
                    payload = {
                        **payload,
                        "writing_intent": materialize_writing_intent_from_contract(
                            contract,
                            merge_state(state, input_payload=payload, mission=mission),
                            mission=mission,
                        ),
                    }
            if steer_replan_ready or not contract_blocks_writing(payload):
                payload = apply_mission_step_to_payload(
                    merge_state(state, input_payload=payload, mission=mission)
                )
        payload["mission_handoff_completed"] = True
        payload["mission_handoff_source"] = source
        payload.pop("enable_planning_mission_handoff", None)
        payload["skip_planning_llm"] = True
        from app.services.mission_steer import steer_requires_planning

        if steer_requires_planning(payload):
            payload["skip_planning_llm"] = False
        exec_mode = str(state.get("execution_mode") or "")
        if str(mission.get("kind", "")).lower() == "writing":
            from app.services.mission_service import _writing_execution_mode

            exec_mode = _writing_execution_mode()
        return merge_state(
            state,
            input_payload=payload,
            mission=mission,
            status=TaskStatus.MISSION_RUNNING.value,
            execution_mode=exec_mode or state.get("execution_mode"),
        )

    updated = init_mission_state(state, payload)
    payload_after = dict(updated.get("input_payload") or {})
    payload_after["mission_handoff_completed"] = True
    payload_after["mission_handoff_source"] = source
    payload_after.pop("enable_planning_mission_handoff", None)
    return merge_state(
        updated,
        input_payload=payload_after,
        status=TaskStatus.MISSION_RUNNING.value,
    )
