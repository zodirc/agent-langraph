from __future__ import annotations

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.state_store import get_state_store


def rejected_node(state: AgentState) -> AgentState:
    """Terminal node when policy rejects (REJECT). ESCALATE routes to human_review."""
    updated = merge_state(
        state,
        status=TaskStatus.REJECTED.value,
        current_node="rejected",
        audit_log=append_audit(
            state,
            "rejected",
            "terminated",
            {"policy_result": state.get("policy_result")},
        ),
    )
    get_state_store().save(updated)
    return updated
