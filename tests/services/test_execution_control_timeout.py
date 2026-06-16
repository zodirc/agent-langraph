"""Control exceptions map watchdog cancel to TIMED_OUT."""

from app.runtime.state import TaskStatus, create_initial_state
from app.services.execution_control import CancelRequested, handle_control_exception
from app.services.task_control import clear_all_task_control_for_tests, register_task_control, request_cancel


def test_cancel_watchdog_reason_maps_to_timed_out(isolated_stores):
    clear_all_task_control_for_tests()
    task_id = "ctrl-timeout"
    state = create_initial_state(task_id=task_id)
    isolated_stores.save(state)
    register_task_control(task_id, "run-1")
    request_cancel(task_id, requested_by="turn_watchdog", reason="turn_wall_clock_budget_exceeded")

    handled = handle_control_exception(state, CancelRequested("turn_wall_clock_budget_exceeded"))
    assert handled is not None
    assert handled.get("status") == TaskStatus.TIMED_OUT.value
    assert "超时" in str(handled.get("final_answer") or "")
