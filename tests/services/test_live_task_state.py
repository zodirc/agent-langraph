from app.runtime.state import create_initial_state, merge_state
from app.services.live_task_state import (
    clear_all_live_for_tests,
    clear_live,
    get_live,
    register_live,
    touch_live,
    touch_live_if_active,
)


def setup_function() -> None:
    clear_all_live_for_tests()


def test_live_register_touch_clear():
    state = create_initial_state()
    register_live(state)
    entry = get_live(state["task_id"])
    assert entry is not None
    assert entry.running is True

    updated = merge_state(state, status="PLANNED", current_node="planning")
    touch_live(updated)
    entry2 = get_live(state["task_id"])
    assert entry2 is not None
    assert entry2.state["status"] == "PLANNED"

    clear_live(state["task_id"])
    assert get_live(state["task_id"]) is None


def test_touch_live_if_active_only_when_registered():
    state = create_initial_state()
    touch_live_if_active(state["task_id"], merge_state(state, status="X"))
    assert get_live(state["task_id"]) is None

    register_live(state)
    touch_live_if_active(
        state["task_id"],
        merge_state(state, status="PLANNED", current_node="planning"),
    )
    entry = get_live(state["task_id"])
    assert entry is not None
    assert entry.state.get("status") == "PLANNED"
    assert entry.state.get("current_node") == "planning"
