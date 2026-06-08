"""Unit tests for unified user event classification (WP-1.1)."""

from __future__ import annotations

import pytest

from app.runtime.state import create_initial_state, merge_state
from app.services.event_classification import VALID_EVENT_TYPES, classify_user_event


@pytest.mark.parametrize(
    ("payload", "state_patch", "expected"),
    [
        ({}, {}, "new_task"),
        ({"event_type": "interrupt"}, {}, "interrupt"),
        ({"goal": "补充一点背景：主角是工程师"}, {"session_turn": 2}, "clarification"),
        (
            {"goal": "你好"},
            {
                "session_turn": 1,
                "conversation_history": [{"role": "user", "content": "你好"}],
            },
            "new_task",
        ),
        (
            {"goal": "停止", "preempt": True, "priority": 100},
            {},
            "interrupt",
        ),
        (
            {
                "goal": "改走悬疑线",
                "replace_goal": True,
            },
            {},
            "redirect",
        ),
        ({"confirm": True}, {}, "confirm"),
        (
            {"intervention": {"action": "reject"}},
            {},
            "reject",
        ),
        ({"resume": True}, {}, "resume"),
        (
            {"resume_checkpoint_ref": "ckpt-1"},
            {},
            "resume",
        ),
        ({"goal": "你正在做什么"}, {}, "status_query"),
    ],
)
def test_classify_user_event_matrix(payload, state_patch, expected):
    state = create_initial_state(input_payload={"goal": "initial"})
    if state_patch:
        state = merge_state(state, **state_patch)
    result = classify_user_event(state, payload=payload)
    assert result.event_type == expected
    assert result.event_id
    assert result.event_type in VALID_EVENT_TYPES


def test_interrupt_preempts_redirect(base_state):
    payload = {
        "goal": "停止并重写大纲",
        "preempt": True,
        "replace_goal": True,
        "priority": 100,
    }
    result = classify_user_event(base_state, payload=payload)
    assert result.event_type == "interrupt"


def test_resume_before_clarification_on_continue_signal(base_state):
    payload = {"goal": "继续写下一章"}
    state = merge_state(
        base_state,
        session_turn=3,
        mission={"goal": "novel"},
    )
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "resume"


def test_prepare_session_turn_session_enabled_turn_two_is_clarification(
    isolated_stores, monkeypatch
):
    """Multi-turn session: turn 2+ classifies as clarification."""
    from app.nodes.event_classification_node import event_classification_node
    from app.runtime.state import TaskStatus, merge_state
    from app.services.session_turn import prepare_session_turn
    from app.services.state_store import get_state_store

    monkeypatch.setattr("app.services.session_turn.settings.SESSION_ENABLED", True)

    session_id = "sess-turn-2"
    state1, created = prepare_session_turn(
        session_id=session_id,
        user_id="tester",
        task_type="qa",
        payload={"goal": "hello"},
    )
    assert created is True
    state1 = merge_state(
        state1,
        session_id=session_id,
        status=TaskStatus.COMPLETED.value,
    )
    get_state_store().save(state1)

    state2, created2 = prepare_session_turn(
        session_id=session_id,
        user_id="tester",
        task_type="qa",
        payload={"goal": "补充一点背景：主角是工程师"},
    )
    assert created2 is False
    assert int(state2.get("session_turn") or 0) == 2

    classified = event_classification_node(state2)
    assert classified["event_type"] == "clarification"


def test_prepare_session_turn_session_enabled_turn_one_is_new_task(
    isolated_stores, monkeypatch
):
    from app.nodes.event_classification_node import event_classification_node
    from app.services.session_turn import prepare_session_turn

    monkeypatch.setattr("app.services.session_turn.settings.SESSION_ENABLED", True)

    state, created = prepare_session_turn(
        session_id="sess-turn-1",
        user_id="tester",
        task_type="qa",
        payload={"goal": "你好"},
        new_session=True,
    )
    assert created is True
    assert int(state.get("session_turn") or 0) == 1

    classified = event_classification_node(state)
    assert classified["event_type"] == "new_task"


def test_prepare_session_turn_first_message_is_new_task(isolated_stores, monkeypatch):
    """E2E: real API entry must not misclassify turn-1 prepopulated history."""
    from app.nodes.acknowledge_node import acknowledge_node
    from app.nodes.event_classification_node import event_classification_node
    from app.services.foreground_ack import build_foreground_ack
    from app.services.session_turn import prepare_session_turn

    monkeypatch.setattr("app.services.session_turn.settings.SESSION_ENABLED", False)

    state, created = prepare_session_turn(
        session_id=None,
        user_id="tester",
        task_type="qa",
        payload={"goal": "你好"},
    )
    assert created is True
    assert int(state.get("session_turn") or 0) == 1
    assert state.get("conversation_history")

    classified = event_classification_node(state)
    assert classified["event_type"] == "new_task"

    ack = build_foreground_ack(classified)
    assert ack["recognized_intent"] == "新任务"
    assert ack["detected_replan"] is False

    acknowledged = acknowledge_node(classified)
    payload = acknowledged.get("input_payload") or {}
    assert payload.get("foreground_ack", {}).get("event_type") == "new_task"


def test_event_classification_node_sets_state(base_state):
    from app.nodes.event_classification_node import event_classification_node

    updated = event_classification_node(base_state)
    assert updated["current_node"] == "event_classification"
    assert updated["event_type"] == "new_task"
    assert updated["event_id"]
    detail = (updated.get("input_payload") or {}).get("event_classification") or {}
    assert detail.get("event_type") == "new_task"
    assert any(entry.get("node") == "event_classification" for entry in updated["audit_log"])
