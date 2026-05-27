from app.nodes.output_node import output_node
from app.runtime.state import TaskStatus, merge_state


def test_output_node_formats_response(base_state):
    state = merge_state(
        base_state,
        reasoning_result={"summary": "Done", "confidence": 0.9, "risk_level": "LOW"},
        policy_result="CONTINUE",
        status=TaskStatus.REVIEW_RESOLVED.value,
    )
    result = output_node(state)
    assert result["status"] == TaskStatus.COMPLETED.value
    assert result["final_answer"] == "Done"
    assert result["structured_output"] is not None
    assert result["artifacts"] is not None
