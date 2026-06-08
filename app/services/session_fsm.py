"""Session FSM — single routing source (optimization.md §4).

External projection: IDLE | RUNNING | REPLANNING | WAITING_USER.
Legacy steer/supersede flags derive from fsm_state; routing reads fsm_state only.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from app.runtime.state import AgentState, merge_state

FsmState = Literal["IDLE", "RUNNING", "REPLANNING", "WAITING_USER"]

FSM_IDLE: FsmState = "IDLE"
FSM_RUNNING: FsmState = "RUNNING"
FSM_REPLANNING: FsmState = "REPLANNING"
FSM_WAITING_USER: FsmState = "WAITING_USER"

VALID_FSM_STATES: frozenset[str] = frozenset(
    {FSM_IDLE, FSM_RUNNING, FSM_REPLANNING, FSM_WAITING_USER}
)


def _explicit_fsm_state(state: AgentState | dict[str, Any]) -> Optional[FsmState]:
    payload = state.get("input_payload") or {}
    raw = payload.get("fsm_state") or state.get("fsm_state")
    if raw in VALID_FSM_STATES:
        return raw  # type: ignore[return-value]
    return None


def _legacy_replan_pending(state: AgentState | dict[str, Any]) -> bool:
    """Legacy replan snapshot — must not call get_fsm_state (no recursion)."""
    payload = state.get("input_payload") or {}
    from app.services.turn_contract_lifecycle import contract_replan_required

    if contract_replan_required(payload):
        return True
    if payload.get("require_planning_after_steer") and not payload.get("steer_planning_done"):
        return True
    if payload.get("foreground_replan_dispatch"):
        return True
    ctx = state.get("interrupt_context") or {}
    if isinstance(ctx, dict):
        op = ctx.get("foreground_operation") or {}
        if isinstance(op, dict):
            from app.services.mission_supersede import (
                FG_STATUS_DISPATCHING,
                FG_STATUS_QUEUED,
                FOREGROUND_KIND_SUPERSEDE,
            )

            if op.get("kind") == FOREGROUND_KIND_SUPERSEDE and op.get("status") in (
                FG_STATUS_QUEUED,
                FG_STATUS_DISPATCHING,
            ):
                return True
    return False


def get_fsm_state(state: AgentState | dict[str, Any]) -> FsmState:
    explicit = _explicit_fsm_state(state)
    if explicit is not None:
        return explicit
    return derive_fsm_from_legacy(state)


def _active_graph_run(state: AgentState | dict[str, Any]) -> bool:
    """True when an execution_run is live in the pool (not cancelled)."""
    run_meta = state.get("execution_run")
    if not isinstance(run_meta, dict) or run_meta.get("cancelled"):
        return False
    run_id = str(run_meta.get("run_id") or "")
    task_id = str(state.get("task_id") or "")
    if not run_id or not task_id:
        return False
    from app.services.graph_run_registry import is_graph_run_active

    return is_graph_run_active(task_id, run_id)


def derive_fsm_from_legacy(state: AgentState | dict[str, Any]) -> FsmState:
    """Bootstrap fsm_state from gates + run metadata (not graph telemetry status)."""
    payload = state.get("input_payload") or {}

    from app.services.mission_steer_confirm import steer_confirmation_pending
    from app.services.mission_steer_outcome_confirm import steer_outcome_confirmation_pending

    if steer_confirmation_pending(payload) or steer_outcome_confirmation_pending(payload):
        return FSM_WAITING_USER
    if bool(state.get("review_required")):
        return FSM_WAITING_USER
    if _legacy_replan_pending(state):
        return FSM_REPLANNING
    if _active_graph_run(state):
        return FSM_RUNNING
    return FSM_IDLE


def set_fsm_state(state: AgentState, fsm_state: FsmState) -> AgentState:
    if fsm_state not in VALID_FSM_STATES:
        raise ValueError(f"invalid fsm_state: {fsm_state}")
    payload = dict(state.get("input_payload") or {})
    payload["fsm_state"] = fsm_state
    return merge_state(state, input_payload=payload, fsm_state=fsm_state)


def sync_legacy_flags_from_fsm(state: AgentState) -> AgentState:
    """Derive legacy replan telemetry from fsm_state (never the reverse)."""
    fsm = get_fsm_state(state)
    payload = dict(state.get("input_payload") or {})
    if fsm == FSM_REPLANNING:
        from app.services.mission_steer import apply_steer_planning_gate

        if not payload.get("require_planning_after_steer"):
            payload = apply_steer_planning_gate(payload)
    elif fsm == FSM_RUNNING:
        payload.pop("foreground_replan_dispatch", None)
    elif fsm == FSM_IDLE:
        if payload.get("steer_planning_done"):
            for key in (
                "require_planning_after_steer",
                "foreground_replan_dispatch",
                "pending_replan",
            ):
                payload.pop(key, None)
    if payload != state.get("input_payload"):
        return merge_state(state, input_payload=payload)
    return state


def routing_needs_replan(state: AgentState | dict[str, Any]) -> bool:
    """Canonical routing check — True when session is in replan phase."""
    explicit = _explicit_fsm_state(state)
    if explicit is not None:
        return explicit == FSM_REPLANNING
    return _legacy_replan_pending(state)


def sync_fsm_state(state: AgentState) -> AgentState:
    """Ensure fsm_state field matches derived legacy state."""
    derived = derive_fsm_from_legacy(state)
    payload = dict(state.get("input_payload") or {})
    explicit = payload.get("fsm_state")
    if derived in (FSM_REPLANNING, FSM_WAITING_USER):
        current = derived
    elif explicit in VALID_FSM_STATES:
        current = explicit  # type: ignore[assignment]
    else:
        current = derived
    if payload.get("fsm_state") != current:
        payload["fsm_state"] = current
        state = merge_state(state, input_payload=payload, fsm_state=current)
    else:
        state = merge_state(state, fsm_state=current)
    return sync_legacy_flags_from_fsm(state)


def transition_fsm(state: AgentState, fsm_state: FsmState) -> AgentState:
    """Apply FSM transition and sync legacy replan flags where needed."""
    updated = set_fsm_state(state, fsm_state)
    payload = dict(updated.get("input_payload") or {})
    if fsm_state == FSM_REPLANNING:
        from app.services.mission_steer import apply_steer_planning_gate

        if not payload.get("require_planning_after_steer"):
            payload = apply_steer_planning_gate(payload)
            updated = merge_state(updated, input_payload=payload)
    elif fsm_state == FSM_RUNNING:
        payload.pop("foreground_replan_dispatch", None)
        updated = merge_state(updated, input_payload=payload)
    return updated


def session_mode(state: AgentState | dict[str, Any]) -> str:
    """Explicit session mode: chat | mission."""
    payload = state.get("input_payload") or {}
    mode = str(payload.get("session_mode") or "").strip().lower()
    if mode in ("chat", "mission"):
        return mode
    if state.get("mission"):
        return "mission"
    return "chat"


def stamp_session_mode(payload: dict[str, Any], *, mode: Optional[str] = None) -> dict[str, Any]:
    out = dict(payload)
    if mode:
        out["session_mode"] = mode
    elif not out.get("session_mode"):
        if out.get("mission") or str(out.get("execution_mode") or "") == "mission":
            out["session_mode"] = "mission"
        else:
            out["session_mode"] = "chat"
    return out


def is_fsm_replanning(state: AgentState | dict[str, Any]) -> bool:
    return get_fsm_state(state) == FSM_REPLANNING


def is_fsm_waiting(state: AgentState | dict[str, Any]) -> bool:
    return get_fsm_state(state) == FSM_WAITING_USER


def is_fsm_running(state: AgentState | dict[str, Any]) -> bool:
    return get_fsm_state(state) == FSM_RUNNING
