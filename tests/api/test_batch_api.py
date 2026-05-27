import time

from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus


def test_create_batch_and_poll(isolated_stores, monkeypatch):
    from app.services import graph_runner

    def fake_start(**kwargs):
        from app.runtime.state import create_initial_state, merge_state

        state = create_initial_state(
            user_id=kwargs.get("user_id", "u"),
            task_type=kwargs.get("task_type", "qa"),
            input_payload=kwargs.get("input_payload", {}),
        )
        return merge_state(state, status=TaskStatus.COMPLETED.value, current_node="output")

    monkeypatch.setattr(graph_runner.get_graph_runner(), "start_task", fake_start)

    client = TestClient(app)
    resp = client.post(
        "/batches",
        json={
            "tasks": [
                {"input_payload": {"goal": "batch task 1"}},
                {"input_payload": {"goal": "batch task 2"}},
            ]
        },
    )
    assert resp.status_code == 202
    batch_id = resp.json()["batch_id"]

    for _ in range(30):
        detail = client.get(f"/batches/{batch_id}").json()
        if detail["status"] in ("COMPLETED", "PARTIAL", "FAILED"):
            break
        time.sleep(0.1)

    assert detail["completed"] == 2
    assert detail["status"] == "COMPLETED"
