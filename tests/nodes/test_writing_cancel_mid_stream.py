"""Writing cancel mid-stream."""

import pytest

from app.runtime.state import TaskStatus, create_initial_state
from app.services.execution_control import CancelRequested, finalize_control_outcome
from app.services.task_control import clear_all_task_control_for_tests, register_task_control, request_cancel, snapshot_task_control


def setup_function():
    clear_all_task_control_for_tests()


def test_cancel_mid_stream_finalizes_cancelled():
    state = create_initial_state(task_id="w-cancel")
    register_task_control("w-cancel", "run-1")
    request_cancel("w-cancel")
    with pytest.raises(CancelRequested):
        from app.services.execution_control import check_for_control_signal

        check_for_control_signal("w-cancel", raise_on_cancel=True)

    snap = snapshot_task_control("w-cancel")
    updated = finalize_control_outcome(state, snap)
    assert updated["status"] == TaskStatus.CANCELLED.value
