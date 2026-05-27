from unittest.mock import patch

from app.runtime.graph import run_graph
from app.runtime.state import TaskStatus, create_initial_state, merge_state


def test_graph_routes_to_dead_letter_after_retries(isolated_stores):
    state = create_initial_state(
        task_id="dlq-flow-1",
        input_payload={"goal": "test", "risk_level": "LOW"},
    )
    state = merge_state(
        state,
        plan=["use tool X"],
        selected_tools=["missing_tool"],
        retry_count=3,
        status=TaskStatus.TOOL_FAILED.value,
        errors=["tool_execution: fail"],
        current_node="tool_execution",
    )
    with patch("app.nodes.tool_node.get_tool_registry") as mock_registry:
        mock_registry.return_value.invoke.side_effect = KeyError("Unknown tool")
        final = run_graph(state)
    assert final["status"] in (TaskStatus.DEAD_LETTER.value, TaskStatus.TOOL_FAILED.value)
