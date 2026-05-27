from app.nodes.planning_node import planning_node
from app.nodes.retrieval_node import retrieval_node
from app.runtime.state import TaskStatus


def test_retrieval_node_returns_knowledge(base_state):
    planned = planning_node(base_state)
    result = retrieval_node(planned)
    assert result["status"] == TaskStatus.RETRIEVED.value
    assert result["current_node"] == "retrieval"
    assert isinstance(result["retrieved_knowledge"], list)
    assert result["audit_log"][-1]["node"] == "retrieval"
