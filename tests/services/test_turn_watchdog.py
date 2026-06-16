"""Turn watchdog clears active graph run registration."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.graph_run_registry import begin_graph_run, clear_all_graph_runs_for_tests, get_active_run_id
from app.services.task_control import clear_all_task_control_for_tests
from app.services.turn_watchdog import clear_watchdog_events_for_tests, finalize_timed_out_task


def test_watchdog_ends_active_graph_run(isolated_stores):
    clear_watchdog_events_for_tests()
    clear_all_task_control_for_tests()
    clear_all_graph_runs_for_tests()
    task_id = "watchdog-end-run"
    state = merge_state(
        create_initial_state(task_id=task_id),
        status=TaskStatus.RUNNING.value,
        current_node="tool_execution",
    )
    isolated_stores.save(state)
    begin_graph_run(task_id)
    assert get_active_run_id(task_id) is not None

    assert finalize_timed_out_task(task_id) is True
    assert get_active_run_id(task_id) is None

    loaded = isolated_stores.load(task_id)
    assert loaded is not None
    assert loaded.get("status") == TaskStatus.TIMED_OUT.value
    run_meta = loaded.get("execution_run") or {}
    assert run_meta.get("cancelled") is True
