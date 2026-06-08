"""Unified reasoning/writing node (WP-4.2)."""

from __future__ import annotations

from app.config.settings import settings
from app.nodes.reasoning_node import reasoning_node
from app.nodes.writing_node import writing_node
from app.runtime.router import _writing_route_allowed
from app.runtime.state import AgentState, TaskStatus
from app.services.state_store import get_state_store


def reasoning_or_writing_node(state: AgentState) -> AgentState:
    """Run writing when writing_intent enabled, else reasoning."""
    if _writing_route_allowed(state):
        updated = writing_node(state)
    else:
        updated = reasoning_node(state)
    updated = dict(updated)
    updated["current_node"] = "reasoning_or_writing"
    get_state_store().save(updated)
    return updated


def route_after_reasoning_or_writing(state: AgentState) -> str:
    status = str(state.get("status", ""))
    if status == TaskStatus.WRITING_FAILED.value:
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "reasoning_or_writing"
    if status in (TaskStatus.FAILED.value, TaskStatus.REASON_FAILED.value):
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "reasoning_or_writing"
    if status.endswith("FAILED") or status == TaskStatus.DEAD_LETTER.value:
        return "dead_letter"
    return "verification"
