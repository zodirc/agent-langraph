"""Post-generation output guard gate (grounding / faithfulness / skill)."""

from __future__ import annotations

from app.nodes.output_guard_node import output_guard_node
from app.runtime.state import AgentState, TaskStatus, merge_state


def apply_post_generation_guard(state: AgentState) -> AgentState:
    status = str(state.get("status") or "")
    if status.endswith("FAILED") or status == TaskStatus.DEAD_LETTER.value:
        return state
    if state.get("output_guard_result") is not None:
        guard = state.get("output_guard_result") or {}
        if guard and not guard.get("passed", True):
            return merge_state(state, status=TaskStatus.REJECTED.value)
        return state
    updated = output_guard_node(state)
    guard = updated.get("output_guard_result") or {}
    if guard and not guard.get("passed", True):
        return merge_state(updated, status=TaskStatus.REJECTED.value)
    return updated
