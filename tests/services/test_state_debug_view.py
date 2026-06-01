from app.runtime.state import create_initial_state, merge_state
from app.services.state_debug_view import build_state_debug_view


def test_build_state_debug_view_truncates_long_lists():
    history = [{"node": f"n{i}", "status": "X", "at": "t"} for i in range(200)]
    state = merge_state(create_initial_state(), node_history=history)
    view = build_state_debug_view(state, truncate=True)
    assert len(view["state"]["node_history"]) == 120
    assert view["truncated"] is True
    assert any("node_history" in item for item in view["truncated_fields"])


def test_build_state_debug_view_full_when_not_truncating():
    state = merge_state(create_initial_state(), final_answer="x" * 20000)
    view = build_state_debug_view(state, truncate=False)
    assert len(view["state"]["final_answer"]) == 20000
