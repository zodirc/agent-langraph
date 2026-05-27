from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.dead_letter_store import get_dead_letter_store
from app.services.metrics_service import get_metrics_service
from app.services.state_store import get_state_store


def dead_letter_node(state: AgentState) -> AgentState:
    """
    Persist exhausted-retry tasks to dead letter queue (architecture §22.4).

    Reads: task_id, errors, retry_count
    Writes: status DEAD_LETTER, audit_log
    """
    updated = merge_state(
        state,
        status=TaskStatus.DEAD_LETTER.value,
        current_node="dead_letter",
        final_answer=state.get("final_answer")
        or f"Task moved to dead letter after {state.get('retry_count', 0)} retries.",
        audit_log=append_audit(
            state,
            "dead_letter",
            "enqueued",
            {"errors": state.get("errors", [])},
        ),
    )
    get_dead_letter_store().enqueue(updated)
    get_state_store().save(updated)
    get_metrics_service().inc_dead_letter()
    return updated
