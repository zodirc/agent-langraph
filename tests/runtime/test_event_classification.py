"""Unit tests for unified user event classification (WP-1.1 / Phase D)."""

from __future__ import annotations

import pytest

from app.runtime.state import create_initial_state, merge_state
from app.services.event_classification import VALID_EVENT_TYPES, classify_user_event


@pytest.mark.parametrize(
    ("payload", "state_patch", "expected"),
    [
        ({}, {}, "new_task"),
        ({"event_type": "interrupt"}, {}, "interrupt"),
        (
            {"goal": "补充一点背景：主角是工程师"},
            {
                "session_turn": 2,
                "conversation_history": [
                    {"role": "user", "content": "写一份谍战剧本"},
                    {"role": "assistant", "content": "好的，开始规划。"},
                    {"role": "user", "content": "补充一点背景：主角是工程师"},
                ],
            },
            "clarification",
        ),
        (
            {"goal": "写一份电影剧本，谍战剧情，要包括细节，民国背景"},
            {
                "session_turn": 2,
                "conversation_history": [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好！"},
                    {"role": "user", "content": "写一份电影剧本，谍战剧情，要包括细节，民国背景"},
                ],
            },
            "new_task",
        ),
        (
            {"goal": "你好"},
            {
                "session_turn": 1,
                "conversation_history": [{"role": "user", "content": "你好"}],
            },
            "new_task",
        ),
        (
            {"goal": "停止"},
            {
                "status": "RUNNING",
                "input_payload": {"fsm_state": "RUNNING", "goal": "写剧本"},
                "_active_run": True,
            },
            "interrupt",
        ),
        (
            {
                "goal": "改走悬疑线",
                "turn_policy_decision": {"intent": "supersede_active_mission"},
            },
            {
                "status": "RUNNING",
                "input_payload": {"fsm_state": "RUNNING", "goal": "写剧本"},
                "_active_run": True,
            },
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
        (
            {"goal": "你能做什么"},
            {},
            "new_task",
        ),
        (
            {"goal": "你正在做什么"},
            {
                "status": "RUNNING",
                "input_payload": {"fsm_state": "RUNNING", "goal": "写剧本"},
                "_active_run": True,
            },
            "status_query",
        ),
    ],
)
def test_classify_user_event_matrix(payload, state_patch, expected):
    from app.services.graph_run_registry import begin_graph_run, end_graph_run

    state = create_initial_state(input_payload={"goal": "initial"})
    active_run = bool((state_patch or {}).pop("_active_run", False))
    if state_patch:
        state = merge_state(state, **state_patch)
    run_id = begin_graph_run(state["task_id"]) if active_run else None
    try:
        result = classify_user_event(state, payload=payload)
    finally:
        if run_id:
            end_graph_run(state["task_id"], run_id)
    assert result.event_type == expected
    assert result.event_id
    assert result.event_type in VALID_EVENT_TYPES


def test_resend_steer_redirect_while_executor_active(base_state):
    """Resend with steer correction while graph is live → redirect."""
    from app.services.graph_run_registry import begin_graph_run, end_graph_run

    steer = "基于原电影编写，人物需要为原电影人物，只改动剧情走向"
    state = merge_state(
        base_state,
        status="RUNNING",
        input_payload={"fsm_state": "RUNNING", "goal": "写剧本"},
    )
    run_id = begin_graph_run(state["task_id"])
    try:
        payload = {"goal": steer, "meta": {"resend": True}}
        result = classify_user_event(state, payload=payload)
    finally:
        end_graph_run(state["task_id"], run_id)
    assert result.event_type == "redirect"
    assert result.source == "user_resend_steer"


def test_interrupt_preempts_redirect(base_state):
    from app.services.graph_run_registry import begin_graph_run, end_graph_run

    payload = {"goal": "停止并重写大纲", "priority": 100}
    mission_state = merge_state(
        base_state,
        status="RUNNING",
        input_payload={"goal": "写剧本", "fsm_state": "RUNNING"},
    )
    run_id = begin_graph_run(mission_state["task_id"])
    try:
        result = classify_user_event(mission_state, payload=payload)
    finally:
        end_graph_run(mission_state["task_id"], run_id)
    assert result.event_type == "interrupt"


def test_preempt_without_active_mission_is_not_interrupt(base_state):
    payload = {
        "goal": "你好",
        "preempt": True,
        "replace_goal": True,
    }
    result = classify_user_event(base_state, payload=payload)
    assert result.event_type == "new_task"


def test_client_routing_hints_stripped(base_state):
    from app.services.graph_run_registry import begin_graph_run, end_graph_run

    payload = {
        "goal": "改走悬疑线",
        "preempt": True,
        "replace_goal": True,
        "turn_policy_decision": {"intent": "supersede_active_mission"},
    }
    state = merge_state(
        base_state,
        status="RUNNING",
        input_payload={"goal": "写剧本", "fsm_state": "RUNNING"},
    )
    run_id = begin_graph_run(state["task_id"])
    try:
        result = classify_user_event(state, payload=payload)
    finally:
        end_graph_run(state["task_id"], run_id)
    assert result.event_type == "redirect"


def test_fsm_replanning_with_goal_is_redirect(base_state):
    state = merge_state(
        base_state,
        input_payload={
            "fsm_state": "REPLANNING",
            "require_planning_after_steer": True,
            "steer_planning_done": False,
            "goal": "旧目标",
        },
    )
    result = classify_user_event(state, payload={"goal": "新剧情方向"})
    assert result.event_type == "redirect"


def test_continue_writing_goal_is_new_turn(base_state):
    """Unified loop: continue cues start a fresh turn, not mission resume."""
    payload = {"goal": "继续写下一章"}
    state = merge_state(
        base_state,
        session_turn=3,
        input_payload={"goal": "novel"},
    )
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "new_task"


def test_prepare_session_turn_session_enabled_turn_two_is_clarification(
    isolated_stores, monkeypatch
):
    """Multi-turn session: turn 2+ supplements an existing substantive task."""
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
        payload={"goal": "写一份谍战剧本"},
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


def test_prepare_session_turn_greeting_then_screenplay_is_new_task(
    isolated_stores, monkeypatch
):
    from app.nodes.acknowledge_node import acknowledge_node
    from app.nodes.event_classification_node import event_classification_node
    from app.runtime.state import TaskStatus, merge_state
    from app.services.foreground_ack import build_foreground_ack
    from app.services.session_turn import prepare_session_turn
    from app.services.state_store import get_state_store

    monkeypatch.setattr("app.services.session_turn.settings.SESSION_ENABLED", True)

    session_id = "sess-greeting-screenplay"
    state1, _ = prepare_session_turn(
        session_id=session_id,
        user_id="tester",
        task_type="qa",
        payload={"goal": "你好"},
    )
    state1 = merge_state(
        state1,
        session_id=session_id,
        status=TaskStatus.COMPLETED.value,
    )
    get_state_store().save(state1)

    screenplay = "写一份电影剧本，谍战剧情，要包括细节，民国背景"
    state2, _ = prepare_session_turn(
        session_id=session_id,
        user_id="tester",
        task_type="qa",
        payload={"goal": screenplay},
    )
    classified = event_classification_node(state2)
    assert classified["event_type"] == "new_task"
    ack = build_foreground_ack(classified)
    assert ack["recognized_intent"] == "新任务"
    acknowledged = acknowledge_node(classified)
    assert (acknowledged.get("input_payload") or {}).get("foreground_ack", {}).get(
        "event_type"
    ) == "new_task"


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


def test_resend_is_new_task_not_resume(base_state):
    goal = "写一份电影剧本，谍战剧情，要包括细节，民国背景"
    payload = {
        "goal": goal,
        "meta": {"resend": True},
        "turn_policy_decision": {"intent": "new_turn"},
    }
    state = merge_state(
        base_state,
        session_turn=3,
        status="COMPLETED",
        input_payload={"goal": goal},
    )
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "new_task"
    assert result.source == "user_resend"


def test_event_classification_node_reuses_stamped_classification(base_state):
    from app.nodes.event_classification_node import event_classification_node

    stamped = {
        "event_type": "new_task",
        "event_id": "evt-stamped-1",
        "source": "prepare",
        "reason": "stamped at ingress",
        "confidence": 1.0,
    }
    state = merge_state(
        base_state,
        session_turn=2,
        input_payload={
            "goal": "写剧本",
            "event_classification": stamped,
            "inbound_event_id": "evt-stamped-1",
        },
    )
    updated = event_classification_node(state)
    assert updated["event_type"] == "new_task"
    assert updated["event_id"] == "evt-stamped-1"


def test_event_classification_node_sets_state(base_state):
    from app.nodes.event_classification_node import event_classification_node

    updated = event_classification_node(base_state)
    assert updated["current_node"] == "event_classification"
    assert updated["event_type"] == "new_task"
    assert updated["event_id"]
    detail = (updated.get("input_payload") or {}).get("event_classification") or {}
    assert detail.get("event_type") == "new_task"
    assert any(entry.get("node") == "event_classification" for entry in updated["audit_log"])
