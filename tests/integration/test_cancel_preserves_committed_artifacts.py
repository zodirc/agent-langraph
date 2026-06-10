"""Cancel preserves committed artifacts."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.execution_control import finalize_control_outcome, mark_step_committed
from app.services.task_control import (
    clear_all_task_control_for_tests,
    register_task_control,
    request_cancel,
    snapshot_task_control,
)


def setup_function():
    clear_all_task_control_for_tests()


def test_cancel_preserves_committed_artifacts(isolated_stores):
    state = merge_state(
        create_initial_state(task_id="cancel-1"),
        status=TaskStatus.RUNNING.value,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1200},
    )
    state = mark_step_committed(
        state,
        {"step_id": "s-outline", "kind": "write_outline"},
        checkpoint_ref="ck-outline",
    )
    isolated_stores.save(state)

    register_task_control("cancel-1", "run-x")
    request_cancel("cancel-1")
    snap = snapshot_task_control("cancel-1")
    finalized = finalize_control_outcome(state, snap)

    assert finalized["status"] == TaskStatus.CANCELLED.value
    assert finalized["manuscript"]["outline_bytes"] == 1200
    assert finalized["interrupt_context"]["last_committed_step"]["checkpoint_ref"] == "ck-outline"
