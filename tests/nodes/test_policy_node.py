import pytest

from app.nodes.policy_node import policy_node
from app.runtime.state import TaskStatus, merge_state


def _state_with_risk(base_state, risk_level: str):
    return merge_state(
        base_state,
        reasoning_result={
            "summary": "test",
            "confidence": 0.9,
            "risk_level": risk_level,
            "structured": {},
        },
        status=TaskStatus.REASONED.value,
    )


@pytest.mark.parametrize(
    "risk_level,expected",
    [
        ("LOW", "CONTINUE"),
        ("HIGH", "REVIEW"),
        ("CRITICAL", "REJECT"),
        ("UNKNOWN", "ESCALATE"),
    ],
)
def test_policy_node_all_paths(base_state, risk_level, expected):
    state = _state_with_risk(base_state, risk_level)
    result = policy_node(state)
    assert result["policy_result"] == expected
    assert result["status"] == TaskStatus.POLICY_CHECKED.value
    assert result["audit_log"][-1]["node"] == "policy"


def test_policy_escalate_sets_review_required(base_state):
    state = _state_with_risk(base_state, "UNKNOWN")
    result = policy_node(state)
    assert result["policy_result"] == "ESCALATE"
    assert result["review_required"] is True
