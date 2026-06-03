from app.runtime.state import create_initial_state, merge_state
from app.services.state_store import StateStore


def test_state_store_preserves_phases_done(test_settings, isolated_stores):
    store = StateStore(test_settings.SQLITE_PATH)
    state = create_initial_state(
        task_id="progress-preserve-1",
        session_id="s",
        user_id="u",
        task_type="writing",
        input_payload={},
    )
    state = merge_state(
        state,
        progress={
            "writing_state": {"phases_done": {"1": ["chapter_summary"]}},
            "metrics": {"written_chars": 100},
        },
    )
    store.save(state)
    snapshot = merge_state(
        state,
        progress={"writing_state": {}, "metrics": {"written_chars": 200}},
    )
    store.save(snapshot)
    loaded = store.load("progress-preserve-1")
    assert loaded is not None
    ws = (loaded.get("progress") or {}).get("writing_state") or {}
    assert ws.get("phases_done") == {"1": ["chapter_summary"]}
