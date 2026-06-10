"""Runtime invariant guard (optimization.md §6 / Phase B).

Called before state_store.save — illegal states cannot persist.
"""

from __future__ import annotations

import os
from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus
from app.services.session_fsm import FSM_REPLANNING, FSM_WAITING_USER, get_fsm_state, session_mode


class InvariantViolation(Exception):
    def __init__(self, invariant_id: str, message: str) -> None:
        self.invariant_id = invariant_id
        super().__init__(f"[{invariant_id}] {message}")


def _strict_mode() -> bool:
    env = os.environ.get("INVARIANT_GUARD_STRICT", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    return settings.APP_ENV != "production"


def validate(state: AgentState) -> AgentState:
    """Validate control-plane invariants; degrade or raise per environment."""
    from app.services.session_fsm import sync_fsm_state

    state = sync_fsm_state(state)
    issues: list[tuple[str, str]] = []

    fsm = get_fsm_state(state)
    payload = state.get("input_payload") or {}
    status = str(state.get("status") or "")

    # I1: single answer for "what is system waiting for"
    from app.services.session_fsm import _legacy_replan_pending

    if _legacy_replan_pending(state) and fsm not in (FSM_REPLANNING, FSM_WAITING_USER):
        issues.append(("I1", f"legacy replan pending but fsm_state={fsm}"))

    # I3: redirect must not leave resume/supersede deadlock markers
    if fsm == FSM_REPLANNING and status == TaskStatus.RUNNING.value:
        if payload.get("execution_grant") and not payload.get("foreground_replan_dispatch"):
            issues.append(("I3", "REPLANNING with execution_grant without dispatch"))

    _ = session_mode(state)

    if not issues:
        return state

    from app.services.metrics_service import get_metrics_service

    for inv_id, msg in issues:
        get_metrics_service().inc_contract_event(f"invariant_{inv_id.lower()}")

    if _strict_mode():
        inv_id, msg = issues[0]
        raise InvariantViolation(inv_id, msg)

    from app.services.session_fsm import set_fsm_state

    return set_fsm_state(state, FSM_WAITING_USER)


def validate_fsm_transition(from_state: str, to_state: str) -> None:
    """Reject illegal FSM transitions (R11)."""
    valid_edges: dict[str, frozenset[str]] = {
        "IDLE": frozenset({"RUNNING", "REPLANNING", "WAITING_USER"}),
        "RUNNING": frozenset({"IDLE", "REPLANNING", "WAITING_USER"}),
        "REPLANNING": frozenset({"RUNNING", "IDLE", "WAITING_USER"}),
        "WAITING_USER": frozenset({"RUNNING", "REPLANNING", "IDLE"}),
    }
    allowed = valid_edges.get(from_state, VALID_FSM_ALL)
    if to_state not in allowed and from_state != to_state:
        raise InvariantViolation("I11", f"illegal fsm transition {from_state} -> {to_state}")


VALID_FSM_ALL = frozenset({"IDLE", "RUNNING", "REPLANNING", "WAITING_USER"})
