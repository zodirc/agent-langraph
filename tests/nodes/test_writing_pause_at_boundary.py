"""Writing node pause at safe boundary."""

import pytest

from app.services.execution_control import PauseRequested
from app.services.task_control import clear_all_task_control_for_tests, register_task_control, request_pause


def setup_function():
    clear_all_task_control_for_tests()


def test_pause_requested_at_append_boundary():
    register_task_control("w-boundary", "run-1")
    request_pause("w-boundary")
    from app.services.execution_control import check_for_control_signal

    with pytest.raises(PauseRequested):
        check_for_control_signal("w-boundary", phase="append_chunk", raise_on_pause=True)
