from app.nodes.planning_node import planning_node
from app.nodes.tool_node import tool_execution_node
from app.runtime.state import TaskStatus


def test_tool_execution_node_runs_tools(base_state):
    planned = planning_node(base_state)
    result = tool_execution_node(planned)
    assert result["status"] == TaskStatus.TOOL_EXECUTED.value
    assert result["current_node"] == "tool_execution"
    assert isinstance(result["tool_results"], list)
    assert result["audit_log"][-1]["action"] == "success"
