"""Routers for event-driven runtime entry (optimization WP-1.1+)."""

from __future__ import annotations

from app.runtime.state import AgentState


def route_after_event_classification(state: AgentState) -> str:
    return "acknowledge"


def route_after_acknowledge(state: AgentState) -> str:
    return "interrupt_control"
