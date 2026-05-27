from unittest.mock import patch

from app.nodes.tool_node import tool_execution_node
from app.runtime.state import TaskStatus, merge_state


def test_tool_node_records_error(base_state, isolated_stores):
    state = merge_state(base_state, selected_tools=["missing_tool"])
    with patch("app.nodes.tool_node.get_tool_registry") as mock_registry:
        mock_registry.return_value.invoke.side_effect = KeyError("Unknown tool")
        result = tool_execution_node(state)
    assert any("tool_execution" in err for err in result["errors"])
    assert result["retry_count"] == 1
    assert result["status"] == "TOOL_FAILED"
