from fastapi.testclient import TestClient

from app.main import app


def test_supervisor_stream_sse(isolated_stores, monkeypatch):
    events = [
        'event: task_created\ndata: {"task_id": "sup-1", "execution_mode": "supervisor"}\n\n',
        'event: subtasks\ndata: {"task_id": "sup-1", "count": 2, "subtasks": [{"domain": "document"}]}\n\n',
        'event: worker\ndata: {"task_id": "sup-1", "domain": "document", "status": "COMPLETED", "summary": "ok"}\n\n',
        'event: node\ndata: {"task_id": "sup-1", "node": "supervisor_merge", "status": "REASONED"}\n\n',
        'event: done\ndata: {"task_id": "sup-1", "status": "COMPLETED", "final_answer": "merged"}\n\n',
    ]

    def fake_stream(**kwargs):
        yield from events

    monkeypatch.setattr(
        "app.api.supervisor_api.get_graph_runner",
        lambda: type("R", (), {"stream_task": lambda self, **kw: fake_stream(**kw)})(),
    )

    client = TestClient(app)
    with client.stream(
        "POST",
        "/supervisor/tasks/stream",
        json={"goal": "analyze docs and code"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: subtasks" in body
    assert "event: worker" in body
    assert "merged" in body
