"""记忆写回 — invoked by close_turn_async after delivered (no longer on graph spine)."""

from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.conversation_context import write_turn_memories
from app.services.state_store import get_state_store


def memory_writeback_node(state: AgentState) -> AgentState:
    """
    Persist structured episode and session summary to long-term memory.

    Reads: final_answer, turn_facts, task metadata
    Writes: audit_log, current_node (status remains COMPLETED)
    """
    try:
        updated = write_turn_memories(state)
        updated = merge_state(
            updated,
            current_node="memory_writeback",
            audit_log=append_audit(
                updated,
                "memory_writeback",
                "success",
                {"structured_episode": updated.get("status") == TaskStatus.COMPLETED.value},
            ),
        )
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"memory_writeback: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            current_node="memory_writeback",
            audit_log=append_audit(state, "memory_writeback", "error", {"detail": str(exc)}),
        )
