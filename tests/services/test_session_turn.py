from app.runtime.state import TaskStatus
from app.services.session_turn import (
    compress_conversation_history,
    finalize_turn_history,
    graph_thread_id,
    prepare_session_turn,
)
from app.services.state_store import StateStore


def test_prepare_session_continues_same_task(test_settings, isolated_stores, monkeypatch):
    import app.services.session_turn as st_mod

    monkeypatch.setattr(st_mod, "settings", test_settings)
    store = StateStore(test_settings.SQLITE_PATH)

    state1, created1 = prepare_session_turn(
        session_id="sess-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "hello"},
    )
    assert created1 is True
    from app.runtime.state import merge_state

    state1 = merge_state(
        state1,
        status=TaskStatus.COMPLETED.value,
        final_answer="Hi there",
        input_payload={
            **state1.get("input_payload", {}),
            "conversation_history": [{"role": "user", "content": "hello", "at": "t1"}],
        },
    )
    state1 = finalize_turn_history(state1)
    store.save(state1)

    state2, created2 = prepare_session_turn(
        session_id="sess-1",
        user_id="u1",
        task_type="qa",
        payload={"goal": "what did I say?"},
    )
    assert created2 is False
    assert state2["task_id"] == "sess-1"
    assert state2["session_turn"] == 2
    roles = [m["role"] for m in state2["input_payload"]["conversation_history"]]
    assert "user" in roles
    assert "assistant" in roles


def test_graph_thread_id_changes_per_turn():
    state = {
        "task_id": "sess-1",
        "session_turn": 3,
    }
    assert graph_thread_id(state) == "sess-1:t3"


def test_compress_history_truncates(test_settings, monkeypatch):
    import app.services.conversation_context as cc_mod

    test_settings.SESSION_MAX_HISTORY_CHARS = 50
    test_settings.SESSION_COMPRESS_ENABLED = True
    monkeypatch.setattr(cc_mod, "settings", test_settings)
    history = [
        {"role": "user", "content": "a" * 40, "at": "1"},
        {"role": "assistant", "content": "b" * 40, "at": "2"},
        {"role": "user", "content": "c" * 40, "at": "3"},
    ]
    out = compress_conversation_history(history)
    assert len(out) <= len(history)
    assert any(m.get("role") == "system" for m in out) or len(out) < 3


def test_finalize_turn_history_appends_assistant():
    state = {
        "task_id": "t1",
        "session_id": "t1",
        "conversation_history": [{"role": "user", "content": "hi", "at": "1"}],
        "final_answer": "Hello",
    }
    out = finalize_turn_history(state)
    assert out["conversation_history"][-1]["role"] == "assistant"
    assert out["conversation_history"][-1]["content"] == "Hello"
