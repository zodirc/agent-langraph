"""Unified think node.

After the legacy 6-paradigm control plane was collapsed into a single agent
loop, this node no longer forks between "writing" and "reasoning". Writing and
editing documents are ordinary actions executed in the tool loop
(``write_text_artifact`` / ``edit_text_artifact``); this node always produces
the natural-language reasoning/answer. The node name is kept stable for SSE
labels and audit continuity.
"""

from __future__ import annotations

from app.config.settings import settings
from app.nodes.reasoning_node import reasoning_node
from app.runtime.state import AgentState, TaskStatus
from app.services.post_generation_guard import apply_post_generation_guard
from app.services.state_store import get_state_store


def _guard_failed(state: AgentState) -> bool:
    guard = state.get("output_guard_result") or {}
    if state.get("status") == TaskStatus.REJECTED.value:
        return True
    return bool(guard) and not guard.get("passed", True)


def reasoning_or_writing_node(state: AgentState) -> AgentState:
    updated = reasoning_node(state)
    status = str(updated.get("status") or "")
    if not status.endswith("FAILED") and status != TaskStatus.DEAD_LETTER.value:
        updated = apply_post_generation_guard(updated)
    updated = dict(updated)
    updated["current_node"] = "reasoning_or_writing"
    get_state_store().save(updated)
    return updated


def route_after_reasoning_or_writing(state: AgentState) -> str:
    from app.services.reasoning_execution_guard import reasoning_terminal_blocked_reason

    blocked = reasoning_terminal_blocked_reason(state)
    if blocked:
        from app.services.planning_retry_signals import (
            apply_planning_replan_signal,
            can_planning_replan_again,
        )
        from app.services.turn_contract import validate_turn_contract_execution

        if can_planning_replan_again(state):
            issues = validate_turn_contract_execution(state) or [blocked]
            replanned = apply_planning_replan_signal(state, issues=issues)
            get_state_store().save(replanned)
            return "incremental_planning"
    if _guard_failed(state):
        return "rejected"
    status = str(state.get("status", ""))
    if status in (TaskStatus.FAILED.value, TaskStatus.REASON_FAILED.value):
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "reasoning_or_writing"
    if status.endswith("FAILED") or status == TaskStatus.DEAD_LETTER.value:
        return "dead_letter"
    return "verification"
