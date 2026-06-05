"""Tests for task_control registry."""

from app.services.task_control import (
    clear_all_task_control_for_tests,
    register_task_control,
    request_cancel,
    request_interrupt_stream,
    request_pause,
    snapshot_task_control,
    snapshot_worker_control,
)


def setup_function():
    clear_all_task_control_for_tests()


def test_register_and_snapshot():
    register_task_control("t1", "run-1")
    snap = snapshot_task_control("t1")
    assert snap is not None
    assert snap.task_id == "t1"
    assert snap.run_id == "run-1"
    assert not snap.pause_requested


def test_pause_and_cancel_are_independent_flags():
    register_task_control("t2", "run-2")
    request_pause("t2", reason="user stop")
    snap = snapshot_task_control("t2")
    assert snap.pause_requested
    assert not snap.cancel_requested

    register_task_control("t3", "run-3")
    request_cancel("t3")
    snap = snapshot_task_control("t3")
    assert snap.cancel_requested


def test_interrupt_stream_does_not_pause_task():
    register_task_control("t4", "run-4")
    request_interrupt_stream("t4")
    snap = snapshot_task_control("t4")
    assert snap.stream_interrupted
    assert not snap.pause_requested
    assert not snap.cancel_requested


def test_request_on_unknown_task_returns_none():
    assert request_pause("missing") is None


def test_worker_scope_pause_and_cancel():
    register_task_control("t5", "run-5")
    request_pause("t5", worker_id="reviewer-wi1")
    wc = snapshot_worker_control("t5", "reviewer-wi1")
    assert wc is not None
    assert wc.pause_requested
    assert not wc.cancel_requested
    task = snapshot_task_control("t5")
    assert task is not None
    assert not task.pause_requested

    request_cancel("t5", worker_id="reviewer-wi1")
    wc = snapshot_worker_control("t5", "reviewer-wi1")
    assert wc.cancel_requested
