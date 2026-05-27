from app.nodes.human_review_node import human_review_node
from app.runtime.state import TaskStatus, merge_state


def test_human_review_interrupt_and_resume(base_state):
    waiting = human_review_node(
        merge_state(base_state, policy_result="REVIEW", review_required=True)
    )
    assert waiting["status"] == TaskStatus.WAITING_REVIEW.value
    assert waiting["audit_log"][-1]["action"] == "waiting"

    approved = human_review_node(
        merge_state(
            waiting,
            review_feedback={"action": "APPROVE", "comment": "OK"},
        )
    )
    assert approved["status"] == TaskStatus.REVIEW_RESOLVED.value
    assert approved["policy_result"] == "CONTINUE"
