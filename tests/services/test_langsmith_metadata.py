from app.runtime.state import create_initial_state, merge_state
from app.services.langsmith_setup import build_trace_metadata, runnable_config_with_trace


def test_build_trace_metadata_from_state():
    state = merge_state(
        create_initial_state(task_id="t-1"),
        execution_mode="mission",
        reasoning_mode="cot",
        mission={"kind": "writing"},
    )
    meta = build_trace_metadata(state)
    assert meta["task_id"] == "t-1"
    assert meta["execution_mode"] == "mission"
    assert meta["reasoning_mode"] == "cot"
    assert meta["mission_kind"] == "writing"


def test_runnable_config_empty_when_disabled(monkeypatch):
    import app.services.langsmith_setup as mod

    class S:
        LANGSMITH_ENABLED = False
        LANGSMITH_API_KEY = ""

    monkeypatch.setattr(mod, "settings", S())
    assert runnable_config_with_trace({}) == {}
