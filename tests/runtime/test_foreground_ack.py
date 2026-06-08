"""Foreground ACK tests (optimization WP-1.2)."""

from __future__ import annotations

import time

import pytest

from app.nodes.acknowledge_node import acknowledge_node
from app.nodes.event_classification_node import event_classification_node
from app.runtime.state import create_initial_state, merge_state
from app.services.foreground_ack import build_foreground_ack
from app.services.stream_progress import report_ack, set_ack_handler


def test_build_foreground_ack_new_task(base_state):
    state = merge_state(base_state, event_type="new_task", event_id="evt-1")
    ack = build_foreground_ack(state)
    assert ack["phase"] == "ack"
    assert ack["event_type"] == "new_task"
    assert ack["recognized_intent"] == "新任务"
    assert ack["next_step"]
    assert ack["message"]
    assert ack["emitted_at"]


def test_build_foreground_ack_interrupt():
    state = create_initial_state(
        input_payload={"goal": "停止", "event_type": "interrupt"},
    )
    state = merge_state(state, event_type="interrupt", event_id="evt-int")
    ack = build_foreground_ack(state)
    assert ack["detected_interrupt"] is True
    assert ack["detected_replan"] is True
    assert "打断" in ack["message"]


def test_acknowledge_node_sets_foreground_status(base_state):
    classified = event_classification_node(base_state)
    started = time.monotonic()
    updated = acknowledge_node(classified)
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 500
    assert updated["current_node"] == "acknowledge"
    fg = updated.get("foreground_status") or {}
    assert fg.get("phase") == "acknowledged"
    assert isinstance(fg.get("last_ack"), dict)
    payload = updated.get("input_payload") or {}
    assert payload.get("foreground_ack", {}).get("event_type") == "new_task"


def test_report_ack_side_channel(base_state):
    captured: list[dict] = []

    def _capture(item: dict) -> None:
        captured.append(item)

    set_ack_handler(_capture)
    try:
        classified = event_classification_node(base_state)
        acknowledge_node(classified)
    finally:
        set_ack_handler(None)

    assert len(captured) == 1
    assert captured[0]["phase"] == "ack"
    assert captured[0]["event_type"] == "new_task"


@pytest.mark.parametrize(
    "event_type",
    [
        "new_task",
        "clarification",
        "interrupt",
        "redirect",
        "confirm",
        "reject",
        "resume",
        "status_query",
    ],
)
def test_ack_payload_snapshot_fields(event_type):
    state = create_initial_state(input_payload={"goal": "test", "event_type": event_type})
    state = merge_state(state, event_type=event_type, event_id=f"evt-{event_type}")
    ack = build_foreground_ack(state)
    for key in (
        "phase",
        "event_type",
        "recognized_intent",
        "will_retrieve",
        "will_execute_tools",
        "detected_interrupt",
        "detected_replan",
        "next_step",
        "message",
        "emitted_at",
    ):
        assert key in ack

    # Direct report_ack smoke
    captured: list[dict] = []
    set_ack_handler(lambda item: captured.append(item))
    try:
        report_ack(node="acknowledge", ack=ack, task_id=state["task_id"])
    finally:
        set_ack_handler(None)
    assert captured[0]["message"] == ack["message"]
