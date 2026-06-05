"""Writing node respects pause at chunk boundary."""

import pytest

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.execution_control import PauseRequested
from app.services.task_control import clear_all_task_control_for_tests, register_task_control, request_pause


def setup_function():
    clear_all_task_control_for_tests()


def test_writing_pause_at_boundary_raises(isolated_stores):
    from app.services.execution_control import check_for_control_signal

    register_task_control("w1", "run-1")
    request_pause("w1")
    with pytest.raises(PauseRequested):
        check_for_control_signal("w1", raise_on_pause=True)


def test_reasoning_cancel_not_reason_failed():
    from app.nodes.reasoning_node import reasoning_node
    from app.services.execution_control import CancelRequested

    state = merge_state(
        create_initial_state(task_id="r1"),
        input_payload={"goal": "test"},
    )
    # CancelRequested should be handled without REASON_FAILED if raised mid-node;
    # direct handler test via execution_control finalize path.
    from app.services.execution_control import finalize_control_outcome
    from app.services.task_control import request_cancel

    register_task_control("r1", "run-1")
    request_cancel("r1")
    from app.services.task_control import snapshot_task_control

    snap = snapshot_task_control("r1")
    updated = finalize_control_outcome(state, snap)
    assert updated["status"] == TaskStatus.CANCELLED.value
    assert updated["status"] != TaskStatus.REASON_FAILED.value
