from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus


def test_planning_node_generates_plan(base_state):
    result = planning_node(base_state)
    assert result["status"] == TaskStatus.PLANNED.value
    assert result["current_node"] == "planning"
    assert result["plan"] is not None
    assert len(result["plan"]) > 0
    assert any(entry.get("node") == "planning" and entry.get("action") == "success" for entry in result["audit_log"])
