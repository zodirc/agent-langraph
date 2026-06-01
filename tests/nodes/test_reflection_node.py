from app.nodes.reflection_node import reflection_node
from app.runtime.state import TaskStatus, merge_state


def test_reflection_node_rule_critique(base_state):
    state = merge_state(
        base_state,
        reasoning_result={
            "summary": "将要写入文件",
            "confidence": 0.5,
            "risk_level": "LOW",
            "structured": {"fact_warnings": ["future writing claim"]},
        },
        reflection_count=0,
    )
    result = reflection_node(state)
    assert result["reflection_count"] == 1
    assert result["reflection_result"]["retry_reasoning"] is True
    assert result["reflection_result"]["verdict"]["recommended_action"] == "retry_same_step"
    assert result["status"] == TaskStatus.REASONED.value
