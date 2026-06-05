from app.nodes.planning_node import planning_node
from app.nodes.retrieval_node import retrieval_node
from app.runtime.state import TaskStatus


def test_retrieval_node_returns_knowledge(base_state, monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_decision.settings.SKIP_RETRIEVAL_WHEN_NO_TOOLS", False
    )
    planned = planning_node(base_state)
    result = retrieval_node(planned)
    assert result["status"] == TaskStatus.RETRIEVED.value
    assert result["current_node"] == "retrieval"
    assert isinstance(result["retrieved_knowledge"], list)
    assert result["audit_log"][-1]["node"] == "retrieval"
    assert "retrieval_decision" in result
    assert "retrieval_trace" in result
    audit = result["audit_log"][-1].get("detail") or {}
    assert "purpose" in audit or result["retrieval_trace"].get("purpose")
