"""Tests for execution_control protocol."""

import pytest

from app.runtime.state import create_initial_state, merge_state
from app.services.execution_control import (
    CONTROL_PAUSE_REQUESTED,
    CancelRequested,
    PauseRequested,
    apply_checkpoint_to_resume_state,
    check_for_control_signal,
    finalize_control_outcome,
    mark_step_committed,
    persist_control_request_to_state,
)
from app.services.mission_execution import (
    PAUSE_USER_REQUESTED_CANCEL,
    PAUSE_USER_REQUESTED_PAUSE,
)
from app.services.task_control import clear_all_task_control_for_tests, register_task_control, request_cancel, request_pause
from app.runtime.state import TaskStatus


def setup_function():
    clear_all_task_control_for_tests()


def test_check_raises_pause_requested():
    register_task_control("t1", "r1")
    request_pause("t1")
    with pytest.raises(PauseRequested):
        check_for_control_signal("t1", raise_on_pause=True)


def test_check_raises_cancel_requested():
    register_task_control("t1", "r1")
    request_cancel("t1")
    with pytest.raises(CancelRequested):
        check_for_control_signal("t1", raise_on_cancel=True)


def test_finalize_pause_sets_mission_paused():
    state = create_initial_state(task_id="t1")
    register_task_control("t1", "r1")
    request_pause("t1")
    control = check_for_control_signal("t1")
    updated = finalize_control_outcome(state, control)
    assert updated["status"] == TaskStatus.MISSION_PAUSED.value
    assert updated["mission_control"]["pause_reason"] == PAUSE_USER_REQUESTED_PAUSE


def test_finalize_cancel_sets_cancelled():
    state = create_initial_state(task_id="t1")
    register_task_control("t1", "r1")
    request_cancel("t1")
    control = check_for_control_signal("t1")
    updated = finalize_control_outcome(state, control)
    assert updated["status"] == TaskStatus.CANCELLED.value
    assert updated["mission_control"]["pause_reason"] == PAUSE_USER_REQUESTED_CANCEL


def test_persist_control_request_to_state():
    state = create_initial_state(task_id="t1")
    updated = persist_control_request_to_state(state, pause=True, reason="offline")
    ctx = updated["interrupt_context"]
    assert ctx["pause_requested"] is True
    assert ctx["control_state"] == CONTROL_PAUSE_REQUESTED


def test_apply_checkpoint_to_resume_state_skips_committed_item():
    state = merge_state(
        create_initial_state(task_id="t-resume"),
        progress={
            "work_plan": {
                "items": [{"id": "wp1", "status": "running", "committed": True}],
                "completed_ids": [],
            }
        },
        interrupt_context={
            "last_committed_step": {"work_item_id": "wp1", "step_id": "s1"},
            "resume_from_checkpoint": {"checkpoint_ref": "s1:gen1", "step_id": "s1"},
        },
    )
    updated = apply_checkpoint_to_resume_state(state)
    assert updated["input_payload"].get("resume_checkpoint_ref") == "s1:gen1"
    items = updated["progress"]["work_plan"]["items"]
    assert items[0]["status"] == "done"
    assert "wp1" in updated["progress"]["work_plan"]["completed_ids"]


def test_mark_step_committed_updates_work_plan():
    state = merge_state(
        create_initial_state(task_id="t1"),
        progress={
            "work_plan": {
                "items": [
                    {"id": "wp1", "kind": "append_body", "status": "running"},
                ]
            }
        },
    )
    updated = mark_step_committed(
        state,
        {"step_id": "s1", "work_item_id": "wp1", "kind": "append_body"},
        checkpoint_ref="ck1",
    )
    items = updated["progress"]["work_plan"]["items"]
    assert items[0]["committed"] is True
    assert items[0]["checkpoint_ref"] == "ck1"


def test_worker_scope_control_takes_precedence():
    register_task_control("t-w", "r1")
    request_pause("t-w", worker_id="wi-review")
    with pytest.raises(PauseRequested):
        check_for_control_signal("t-w", worker_id="wi-review", raise_on_pause=True)
    # Task-scope not paused
    assert check_for_control_signal("t-w") is not None
    assert not check_for_control_signal("t-w").pause_requested


def test_mark_step_committed_writes_attempt_and_run_id():
    state = merge_state(
        create_initial_state(task_id="t-meta"),
        execution_run={"run_id": "run-abc"},
        progress={
            "work_plan": {
                "items": [{"id": "wp1", "kind": "review_chapter", "status": "running"}],
            }
        },
    )
    updated = mark_step_committed(
        state,
        {
            "step_id": "wp1_review_chapter_ch3",
            "work_item_id": "wp1",
            "kind": "review_chapter",
            "attempt": 2,
            "last_run_id": "run-abc",
        },
        checkpoint_ref="wp1_review_chapter_ch3:gen1",
    )
    row = updated["progress"]["work_plan"]["items"][0]
    assert row["attempt"] == 2
    assert row["last_run_id"] == "run-abc"
