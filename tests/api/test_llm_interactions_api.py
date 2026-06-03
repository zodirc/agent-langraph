from fastapi.testclient import TestClient

from app.main import app
from app.runtime.state import TaskStatus


def test_get_task_llm_interactions(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.runtime.state import create_initial_state, merge_state
    from app.services.llm_interaction_store import get_llm_interaction_store

    store = isolated_stores
    state = merge_state(
        create_initial_state(session_id="sess-llm"),
        status=TaskStatus.COMPLETED.value,
        current_node="output",
    )
    store.save(state)
    task_id = state["task_id"]

    get_llm_interaction_store().record(
        task_id=task_id,
        session_id="sess-llm",
        purpose="planning",
        system_prompt="You are a planner.",
        user_content='{"goal": "hello"}',
        response_text='{"plan": ["step1"]}',
    )
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    resp = client.get(f"/tasks/{task_id}/llm-interactions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["session_id"] == "sess-llm"
    assert body["count"] == 1
    turn = body["interactions"][0]
    assert turn["index"] == 1
    assert turn["purpose"] == "planning"
    assert turn["request"]["messages"][0]["role"] == "system"
    assert "planner" in turn["request"]["messages"][0]["content"]
    assert turn["response"] == '{"plan": ["step1"]}'


def test_get_task_llm_interactions_by_index(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.runtime.state import create_initial_state, merge_state
    from app.services.llm_interaction_store import get_llm_interaction_store

    store = isolated_stores
    state = merge_state(create_initial_state(), status=TaskStatus.COMPLETED.value)
    store.save(state)
    task_id = state["task_id"]
    ix = get_llm_interaction_store()
    for n, purpose in enumerate(["planning", "writing"], start=1):
        ix.record(
            task_id=task_id,
            session_id=task_id,
            purpose=purpose,
            system_prompt="sys",
            user_content=f"u{n}",
            response_text=f"r{n}",
        )
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    resp = client.get(f"/tasks/{task_id}/llm-interactions?index=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["index"] == 2
    assert body["interaction"]["purpose"] == "writing"
    assert body["interaction"]["response"] == "r2"


def test_get_task_llm_interactions_summary(isolated_stores, monkeypatch):
    from app.api import task_api
    from app.runtime.state import create_initial_state, merge_state
    from app.services.llm_interaction_store import get_llm_interaction_store

    store = isolated_stores
    state = merge_state(create_initial_state(), status=TaskStatus.COMPLETED.value)
    store.save(state)
    task_id = state["task_id"]
    get_llm_interaction_store().record(
        task_id=task_id,
        session_id=task_id,
        purpose="planning",
        system_prompt="s",
        user_content="u",
        response_text="r",
    )
    monkeypatch.setattr(task_api, "get_state_store", lambda: store)

    client = TestClient(app)
    resp = client.get(f"/tasks/{task_id}/llm-interactions?summary=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["summaries"][0]["index"] == 1
    assert "request" not in body["summaries"][0]


def test_llm_interaction_trim_per_task(isolated_stores, monkeypatch):
    import app.services.llm_interaction_store as llm_ix_mod
    from app.services.llm_interaction_store import get_llm_interaction_store

    monkeypatch.setattr(llm_ix_mod.settings, "LLM_INTERACTION_LOG_MAX_PER_TASK", 2)
    monkeypatch.setattr(llm_ix_mod.settings, "LLM_INTERACTION_LOG_MAX_BYTES_PER_TASK", 0)
    store = get_llm_interaction_store()
    task_id = "trim-task-1"
    for i in range(4):
        store.record(
            task_id=task_id,
            session_id=task_id,
            purpose=f"p{i}",
            system_prompt="s",
            user_content="u",
            response_text=f"r{i}",
        )
    rows = store.list_for_task(task_id)
    assert len(rows) == 2
    assert rows[0]["response"] == "r2"
    assert rows[1]["response"] == "r3"


def test_llm_interaction_trim_by_bytes(isolated_stores, monkeypatch):
    import app.services.llm_interaction_store as llm_ix_mod
    from app.services.llm_interaction_store import get_llm_interaction_store

    monkeypatch.setattr(llm_ix_mod.settings, "LLM_INTERACTION_LOG_MAX_PER_TASK", 0)
    monkeypatch.setattr(llm_ix_mod.settings, "LLM_INTERACTION_LOG_MAX_BYTES_PER_TASK", 120)
    store = get_llm_interaction_store()
    task_id = "trim-bytes-1"
    store.record(
        task_id=task_id,
        session_id=task_id,
        purpose="a",
        system_prompt="s",
        user_content="u",
        response_text="r1",
    )
    assert store.bytes_for_task(task_id) <= 120
    store.record(
        task_id=task_id,
        session_id=task_id,
        purpose="b",
        system_prompt="s",
        user_content="u",
        response_text="r2",
    )
    rows = store.list_for_task(task_id)
    assert len(rows) == 1
    assert rows[0]["response"] == "r2"


def test_get_task_llm_interactions_not_found(isolated_stores, monkeypatch):
    from app.api import task_api

    monkeypatch.setattr(task_api, "get_state_store", lambda: isolated_stores)
    client = TestClient(app)
    resp = client.get("/tasks/nonexistent-task-id/llm-interactions")
    assert resp.status_code == 404
