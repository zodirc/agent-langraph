"""Post-planning and verification routers (WP-1.4 / WP-3.1)."""

from __future__ import annotations

from app.runtime.planning_gate_router import route_after_incremental_planning
from app.runtime.state import AgentState
from app.services.interrupt_control import should_abort_after_interrupt


def route_after_interrupt_control(state: AgentState) -> str:
    if should_abort_after_interrupt(state):
        return "end"
    return "incremental_planning"


def route_after_context_governance(state: AgentState) -> str:
    return "reasoning_or_writing"


def route_after_verification(state: AgentState) -> str:
    decision = state.get("submission_decision") or {}
    action = str(decision.get("decision") or "submit")
    if action == "retry_reasoning":
        return "reasoning_or_writing"
    if action == "retry_planning":
        return "incremental_planning"
    if action == "reject":
        return "rejected"
    if action == "human_review":
        return "human_review"
    return "policy"


__all__ = [
    "route_after_interrupt_control",
    "route_after_incremental_planning",
    "route_after_context_governance",
    "route_after_verification",
]
