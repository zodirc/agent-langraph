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


def test_mission_session_hello_isolates_writing_mission(test_settings, isolated_stores, monkeypatch):
    import app.services.session_turn as st_mod

    monkeypatch.setattr(st_mod, "settings", test_settings)
    store = StateStore(test_settings.SQLITE_PATH)

    state1, _ = prepare_session_turn(
        session_id="sess-mission",
        user_id="u1",
        task_type="qa",
        payload={
            "goal": "写长篇",
            "execution_mode": "mission",
            "mission": {"kind": "writing", "objective": "写长篇", "autonomous": True},
        },
    )
    from app.runtime.state import merge_state

    state1 = merge_state(
        state1,
        mission={"kind": "writing", "objective": "写长篇"},
        execution_mode="mission",
        manuscript={"body_path": "novel.txt", "body_bytes": 12000},
        status=TaskStatus.MISSION_PAUSED.value,
    )
    store.save(state1)

    state2, created2 = prepare_session_turn(
        session_id="sess-mission",
        user_id="u1",
        task_type="qa",
        payload={"goal": "hello", "mission_auto": True},
    )
    assert created2 is False
    assert state2.get("mission") is None
    assert state2.get("execution_mode") == "single"
    assert state2["input_payload"].get("mission_suspended") is True
    assert state2["input_payload"]["writing_intent"]["enabled"] is False


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


def test_continue_resume_skips_steer_apply(test_settings, isolated_stores, monkeypatch):
    import app.services.session_turn as st_mod

    monkeypatch.setattr(st_mod, "settings", test_settings)
    store = StateStore(test_settings.SQLITE_PATH)

    state1, _ = prepare_session_turn(
        session_id="sess-continue",
        user_id="u1",
        task_type="qa",
        payload={
            "goal": "写一篇谍战小说",
            "execution_mode": "mission",
            "mission": {"kind": "writing", "objective": "暗战"},
        },
    )
    from app.runtime.state import merge_state

    state1 = merge_state(
        state1,
        mission={"kind": "writing", "objective": "暗战"},
        execution_mode="mission",
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload={
            **state1.get("input_payload", {}),
            "turn_contract": {"primary_op": "write_outline"},
            "goal": "写一篇谍战小说",
        },
    )
    store.save(state1)

    state2, created2 = prepare_session_turn(
        session_id="sess-continue",
        user_id="u1",
        task_type="qa",
        payload={"goal": "继续", "mission_auto": True},
    )
    assert created2 is False
    ip = state2.get("input_payload") or {}
    assert ip.get("latest_steer_message") in (None, "")
    assert not ip.get("require_planning_after_steer")
    assert ip.get("execution_grant") or ip.get("mission_suspended") is not True


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
