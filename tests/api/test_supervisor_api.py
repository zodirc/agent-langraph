from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus, merge_state


def test_create_supervisor_task(isolated_stores, monkeypatch):
    from app.services import graph_runner

    def fake_supervisor(**kwargs):
        from app.runtime.state import create_initial_state
        from app.services.state_store import get_state_store

        state = create_initial_state(
            user_id=kwargs.get("user_id", "u"),
            task_type="supervisor",
            input_payload=kwargs.get("input_payload", {}),
        )
        final = merge_state(
            state,
            execution_mode="supervisor",
            status=TaskStatus.COMPLETED.value,
            current_node="memory_writeback",
            final_answer="supervisor done",
            subtasks=[{"domain": "document", "description": "x"}],
            worker_results={"w1": {"summary": "ok"}},
            structured_output={"supervisor": True},
        )
        get_state_store().save(final)
        return final

    monkeypatch.setattr(graph_runner.get_graph_runner(), "start_task", fake_supervisor)

    client = TestClient(app)
    created = client.post(
        "/supervisor/tasks",
        json={"goal": "analyze report and review code"},
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    result = client.get(f"/supervisor/tasks/{task_id}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["worker_results"] is not None
    assert body["final_answer"] == "supervisor done"


def test_list_domains(isolated_stores):
    client = TestClient(app)
    resp = client.get("/domains")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 3
