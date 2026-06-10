"""Integration tests for pause/resume checkpoint flow."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.execution_control import finalize_control_outcome, mark_step_committed
from app.services.graph_runner import get_graph_runner
from app.services.task_control import clear_all_task_control_for_tests, register_task_control, request_pause


def setup_function():
    clear_all_task_control_for_tests()


def test_pause_resume_checkpoint_context(isolated_stores):
    state = merge_state(
        create_initial_state(task_id="pause-resume-1"),
        status=TaskStatus.RUNNING.value,
        progress={"work_plan": {"items": [{"id": "wp1", "status": "running"}]}},
    )
    state = mark_step_committed(
        state,
        {"step_id": "s1", "work_item_id": "wp1", "kind": "append_body"},
        checkpoint_ref="ck-ref-1",
    )
    isolated_stores.save(state)

    register_task_control("pause-resume-1", "run-a")
    request_pause("pause-resume-1")
    runner = get_graph_runner()
    resp = runner.pause_task("pause-resume-1")
    assert resp["accepted"] is True
    assert resp["control_action"] == "pause_task"

    stored = isolated_stores.load("pause-resume-1")
    assert stored["interrupt_context"]["last_committed_step"]["checkpoint_ref"] == "ck-ref-1"

    control = register_task_control("pause-resume-1", "run-b")
    from app.services.task_control import request_pause as rp

    rp("pause-resume-1")
    from app.services.task_control import snapshot_task_control

    snap = snapshot_task_control("pause-resume-1")
    finalized = finalize_control_outcome(stored, snap)
    assert finalized["status"] == TaskStatus.PAUSED.value
    assert finalized["interrupt_context"]["resume_from_checkpoint"]["checkpoint_ref"] == "ck-ref-1"
