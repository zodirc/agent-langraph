"""Interrupt control node — explicit runtime state machine (WP-1.3)."""

from __future__ import annotations

from app.runtime.state import AgentState
from app.services.interrupt_control import apply_interrupt_control, should_abort_after_interrupt
from app.services.state_store import get_state_store


def interrupt_control_node(state: AgentState) -> AgentState:
    updated = apply_interrupt_control(state)
    get_state_store().save(updated)
    return updated
