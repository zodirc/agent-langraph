from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus


def test_create_and_query_task(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.services import graph_runner

    store = isolated_stores

    def fake_start(**kwargs):
        from app.runtime.state import create_initial_state, merge_state

        state = create_initial_state(
            user_id=kwargs.get("user_id", "anonymous"),
            task_type=kwargs.get("task_type", "qa"),
            input_payload=kwargs.get("input_payload", {}),
        )
        final = merge_state(
            state,
            status=TaskStatus.COMPLETED.value,
            current_node="memory_writeback",
            final_answer="ok",
            structured_output={"ok": True},
            artifacts=[],
            audit_log=[{"node": "output", "action": "success"}],
        )
        store.save(final)
        return final

    monkeypatch.setattr(graph_runner.get_graph_runner(), "start_task", fake_start)
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    created = client.post(
        "/tasks",
        json={"task_type": "qa", "user_id": "u1", "input_payload": {"goal": "hi"}},
    )
    assert created.status_code == 201
    body = created.json()
    assert "task_id" in body
    assert body["status"] == TaskStatus.COMPLETED.value

    status_resp = client.get(f"/tasks/{body['task_id']}/status")
    assert status_resp.status_code == 200

    result_resp = client.get(f"/tasks/{body['task_id']}/result")
    assert result_resp.status_code == 200
    assert result_resp.json()["final_answer"] == "ok"


def test_get_task_state_debug(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.runtime.state import create_initial_state, merge_state

    store = isolated_stores
    state = merge_state(
        create_initial_state(
            session_id="sess-1",
            input_payload={"goal": "debug me"},
        ),
        status=TaskStatus.PLANNED.value,
        current_node="planning",
        plan=["retrieval", "reasoning"],
    )
    store.save(state)
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    resp = client.get(f"/tasks/{state['task_id']}/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == state["task_id"]
    assert body["session_id"] == state["session_id"]
    assert body["state"]["plan"] == ["retrieval", "reasoning"]
    assert "field_summary" in body
    assert "plan" in body["field_summary"]
    assert body["store_available"] is True
    assert body["sources"]["store"] is not None
    assert body["live_available"] is False


def test_get_task_context_composition(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.runtime.state import create_initial_state, merge_state

    store = isolated_stores
    state = merge_state(
        create_initial_state(
            session_id="sess-cg",
            input_payload={"goal": "composition test"},
        ),
        conversation_history=[{"role": "user", "content": "hi", "at": "t0"}],
    )
    store.save(state)
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    resp = client.get(
        f"/tasks/{state['task_id']}/context-composition",
        params={"purpose": "planning"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == state["task_id"]
    assert body["purpose"] == "planning"
    assert "composition" in body


def test_post_task_context_compress(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.runtime.state import create_initial_state, merge_state

    store = isolated_stores
    state = merge_state(
        create_initial_state(session_id="sess-cc"),
        conversation_history=[{"role": "user", "content": "long " * 200, "at": "t"}],
    )
    store.save(state)
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    resp = client.post(
        f"/tasks/{state['task_id']}/context/compress",
        json={"scope": "transcript", "token_budget": 8000},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == state["task_id"]
    assert "kept" in body


def test_get_task_state_debug_not_found(isolated_stores, monkeypatch):
    from app.api import task_api

    monkeypatch.setattr(task_api, "get_state_store", lambda: isolated_stores)
    client = TestClient(app)
    resp = client.get("/tasks/missing-task/state")
    assert resp.status_code == 404


def test_create_task_invalid_payload_returns_error(isolated_stores, monkeypatch):
    from app.services import graph_runner

    monkeypatch.setattr(
        graph_runner.get_graph_runner(),
        "start_task",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = TestClient(app)
    resp = client.post("/tasks", json={"task_type": "qa", "input_payload": {}})
    assert resp.status_code == 500
