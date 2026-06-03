from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus


def test_stream_task_sse(isolated_stores, monkeypatch):
    events = [
        'event: task_created\ndata: {"task_id": "stream-1", "status": "NEW"}\n\n',
        'event: node\ndata: {"task_id": "stream-1", "node": "planning", "status": "PLANNED"}\n\n',
        'event: done\ndata: {"task_id": "stream-1", "status": "COMPLETED", "final_answer": "hello"}\n\n',
    ]

    def fake_stream(**kwargs):
        yield from events

    monkeypatch.setattr(
        "app.api.task_api.get_graph_runner",
        lambda: type("R", (), {"stream_task": lambda self, **kw: fake_stream(**kw)})(),
    )

    client = TestClient(app)
    with client.stream(
        "POST",
        "/tasks/stream",
        json={"task_type": "qa", "input_payload": {"goal": "test"}},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: task_created" in body
    assert "event: done" in body
    assert "COMPLETED" in body


def test_list_tasks(isolated_stores):
    from app.runtime.state import create_initial_state
    from app.services.state_store import get_state_store

    store = get_state_store()
    state = create_initial_state(task_id="list-1", input_payload={"goal": "first task"})
    store.save(state)

    client = TestClient(app)
    resp = client.get("/tasks?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(t["task_id"] == "list-1" for t in data["tasks"])


def test_platform_home():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Skill-Driven Agent Platform" in resp.text


def test_web_chat_page():
    client = TestClient(app)
    resp = client.get("/chat")
    assert resp.status_code == 200
    assert "agent-langraph — 对话" in resp.text


def test_web_cli_redirect():
    client = TestClient(app)
    resp = client.get("/cli", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/chat"
