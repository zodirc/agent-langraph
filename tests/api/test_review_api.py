from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.state_store import get_state_store


def test_submit_review_resumes_task(isolated_stores, monkeypatch):
    state = merge_state(
        create_initial_state(task_id="review-task-1"),
        status=TaskStatus.WAITING_REVIEW.value,
        policy_result="REVIEW",
        review_required=True,
        current_node="human_review",
    )
    get_state_store().save(state)

    from app.services import graph_runner

    def fake_resume(task_id, action, comment=""):
        return merge_state(
            get_state_store().load(task_id),
            status=TaskStatus.COMPLETED.value,
            current_node="memory_writeback",
            final_answer="approved",
        )

    monkeypatch.setattr(graph_runner.get_graph_runner(), "submit_review", fake_resume)

    client = TestClient(app)
    resp = client.post(
        "/reviews",
        json={"task_id": "review-task-1", "action": "APPROVE", "comment": "OK"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == TaskStatus.COMPLETED.value
