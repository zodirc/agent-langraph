from app.nodes.output_guard_node import output_guard_node
from app.runtime.state import TaskStatus, merge_state


def test_output_guard_blocks_pii(base_state):
    state = merge_state(
        base_state,
        reasoning_result={
            "summary": "联系 admin@secret.com",
            "confidence": 0.9,
            "risk_level": "LOW",
        },
        status=TaskStatus.REASONED.value,
    )
    result = output_guard_node(state)
    assert result["output_guard_result"]["passed"] is False
    assert result["status"] == TaskStatus.REJECTED.value
