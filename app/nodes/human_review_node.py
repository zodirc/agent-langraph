"""人机审核
有 review_feedback → 合并后走 output；无反馈 → 暂停图执行。

Human review: interrupt before this node; WAITING_REVIEW until feedback.
With feedback → continue to output; without → graph interrupt (resume_graph)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.runtime.state import AgentState, TaskStatus, append_audit, merge_state
from app.services.metrics_service import get_metrics_service
from app.services.state_store import get_state_store


def human_review_node(state: AgentState) -> AgentState:
    """
    Pause for human review or apply submitted review feedback.

    Reads: review_feedback, policy_result
    Writes: review_required, status, current_node, audit_log
    """
    try:
        feedback = state.get("review_feedback")
        if feedback:
            action = str(feedback.get("action", "")).upper()
            if action == "REJECT":
                updated = merge_state(
                    state,
                    policy_result="REJECT",
                    review_required=False,
                    status=TaskStatus.REJECTED.value,
                    current_node="human_review",
                    audit_log=append_audit(
                        state,
                        "human_review",
                        "rejected",
                        {"comment": feedback.get("comment")},
                    ),
                )
            else:
                get_metrics_service().inc_review_resolved()
                updated = merge_state(
                    state,
                    policy_result="CONTINUE",
                    review_required=False,
                    status=TaskStatus.REVIEW_RESOLVED.value,
                    current_node="human_review",
                    audit_log=append_audit(
                        state,
                        "human_review",
                        "approved",
                        {"comment": feedback.get("comment")},
                    ),
                )
        else:
            updated = merge_state(
                state,
                review_required=True,
                status=TaskStatus.WAITING_REVIEW.value,
                current_node="human_review",
                review_requested_at=datetime.now(timezone.utc).isoformat(),
                audit_log=append_audit(state, "human_review", "waiting"),
            )
            get_metrics_service().inc_review_waiting()
        get_state_store().save(updated)
        return updated
    except Exception as exc:
        return merge_state(
            state,
            errors=list(state.get("errors", [])) + [f"human_review: {exc}"],
            retry_count=state.get("retry_count", 0) + 1,
            status=TaskStatus.FAILED.value,
            current_node="human_review",
            audit_log=append_audit(state, "human_review", "error", {"detail": str(exc)}),
        )
