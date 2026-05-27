from app.runtime.state import TaskStatus
from app.services.graph_runner import GraphRunner


def test_graph_flow_completes_low_risk(isolated_stores, test_settings, monkeypatch):
    runner = GraphRunner()
    state = runner.start_task(
        user_id="tester",
        task_type="qa",
        input_payload={"goal": "Explain agent runtime", "risk_level": "LOW", "needs_search": True},
    )
    assert state["status"] in (
        TaskStatus.COMPLETED.value,
        TaskStatus.WAITING_REVIEW.value,
    )
    assert len(state["audit_log"]) > 0
    if state["status"] == TaskStatus.COMPLETED.value:
        assert state["final_answer"] is not None


def test_graph_flow_high_risk_waits_review(isolated_stores):
    runner = GraphRunner()
    state = runner.start_task(
        user_id="tester",
        input_payload={"goal": "Dangerous operation", "risk_level": "HIGH"},
    )
    assert state["status"] == TaskStatus.WAITING_REVIEW.value
    assert state["policy_result"] == "REVIEW"
