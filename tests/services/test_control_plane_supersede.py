"""Control-plane supersede fix — (status × event) matrix (docs/control-plane-supersede-fix.md)."""

from __future__ import annotations

import pytest

from app.runtime.state import TaskStatus, merge_state
from app.services.event_classification import classify_user_event
from app.services.graph_runner import GraphRunner
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import apply_steer_planning_gate, queue_steer_message
from app.services.session_controller import SessionController
from app.services.state_store import get_state_store

P0_STEER = (
    "我认为你需要使用原电影的人物，只是在一些原电影的剧情走向上改动，也不需要架空人物"
)


def _completed_mission_state(base_state, *, goal: str = "写暗战同人"):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    return merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.COMPLETED.value,
        session_turn=1,
        input_payload={"goal": goal, "mission": mission},
    )


def _running_mission_state(base_state, *, goal: str = "写暗战同人"):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    return merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_RUNNING.value,
        input_payload={
            "goal": goal,
            "mission": mission,
            "fsm_state": "RUNNING",
        },
    )


def test_completed_p0_resend_classified_as_new_task(base_state):
    """COMPLETED + 纠偏 P0 + resend → new_task (not heuristic interrupt)."""
    state = _completed_mission_state(base_state)
    payload = {
        "goal": P0_STEER,
        "meta": {"resend": True},
        "mission": state.get("mission"),
    }
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "new_task"
    assert result.source == "user_resend"


def test_completed_p0_non_resend_classified_as_interrupt(base_state):
    """COMPLETED + 纠偏 P0 + non-resend → heuristic interrupt (steer path)."""
    state = _completed_mission_state(base_state)
    payload = {"goal": P0_STEER, "mission": state.get("mission")}
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "interrupt"
    assert result.source == "heuristic_interrupt"


def test_running_p0_classified_as_interrupt(base_state):
    """MISSION_RUNNING + P0 content → interrupt."""
    state = _running_mission_state(base_state)
    payload = {"goal": P0_STEER}
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "interrupt"
    assert result.source == "heuristic_interrupt"


def test_running_explicit_stop_beats_resend(base_state):
    """MISSION_RUNNING + explicit stop intervention → interrupt before resend."""
    state = _running_mission_state(base_state)
    payload = {
        "goal": "停止",
        "meta": {"resend": True},
        "intervention": {"action": "stop"},
    }
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "interrupt"
    assert result.source == "explicit_interrupt"


def test_running_p0_goal_resend_beats_heuristic_interrupt(base_state):
    """MISSION_RUNNING + P0 goal text + resend → new_task (resend beats heuristic)."""
    state = _running_mission_state(base_state)
    payload = {
        "goal": "停止",
        "meta": {"resend": True},
    }
    result = classify_user_event(state, payload=payload)
    assert result.event_type == "new_task"
    assert result.source == "user_resend"


def test_prepare_supersede_replan_new_status_safe_fallback(base_state):
    """NEW after completed steer must not raise — degrades to fresh planning."""
    state = _completed_mission_state(base_state)
    state = merge_state(
        state,
        status=TaskStatus.NEW.value,
        session_turn=2,
        input_payload=apply_steer_planning_gate(
            {
                "goal": P0_STEER,
                "latest_steer_message": P0_STEER,
                "mission": state.get("mission"),
                "fsm_state": "REPLANNING",
            }
        ),
    )
    get_state_store().save(state)

    runner = GraphRunner()
    prepared = runner.prepare_supersede_replan(state["task_id"])
    assert prepared["status"] == TaskStatus.NEW.value
    assert prepared.get("execution_mode") == "single"


def test_stream_steer_mission_from_completed_streams_fresh_plan(base_state, monkeypatch):
    """COMPLETED + P0 steer → NEW must stream fresh plan, not fail on supersede guard."""
    state = _completed_mission_state(base_state)
    get_state_store().save(state)

    runner = GraphRunner()
    streamed: list[str] = []

    def fake_stream_single(st, *, created=False):
        assert st["status"] == TaskStatus.NEW.value
        yield 'event: task_created\ndata: {"status":"NEW"}\n\n'
        yield 'event: progress\ndata: {"message":"planning"}\n\n'

    monkeypatch.setattr(runner, "_stream_single", fake_stream_single)
    monkeypatch.setattr(
        runner,
        "stream_supersede_mission",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not call supersede for NEW status")
        ),
    )

    for chunk in runner.stream_steer_mission(state["task_id"], P0_STEER, preempt=True):
        streamed.append(chunk)

    loaded = get_state_store().load(state["task_id"])
    assert loaded["status"] == TaskStatus.NEW.value
    assert any("task_created" in e for e in streamed)
    assert any("progress" in e for e in streamed)
    assert not any("FAILED" in e for e in streamed)


def test_queue_steer_after_completed_then_prepare_supersede_no_crash(base_state):
    """Regression: NEW + routing_needs_replan must not raise in prepare_supersede_replan."""
    state = _completed_mission_state(base_state)
    get_state_store().save(state)

    updated = queue_steer_message(state["task_id"], P0_STEER, replace_goal=True)
    assert updated["status"] == TaskStatus.NEW.value

    runner = GraphRunner()
    prepared = runner.prepare_supersede_replan(state["task_id"])
    assert prepared["status"] == TaskStatus.NEW.value


def _track_dispatch(monkeypatch, ctrl: SessionController) -> dict[str, int]:
    calls = {"stream_task": 0, "stream_steer": 0}

    def fake_stream_task(**_kwargs):
        calls["stream_task"] += 1
        yield 'event: stream_open\ndata: {"phase":"accepted"}\n\n'
        yield 'event: task_created\ndata: {"status":"NEW"}\n\n'
        yield 'event: done\ndata: {"status":"PLANNED"}\n\n'

    def fake_stream_steer(*_a, **_k):
        calls["stream_steer"] += 1
        yield 'event: replan_started\ndata: {"source":"api_supersede"}\n\n'
        yield 'event: done\ndata: {"status":"MISSION_RUNNING"}\n\n'

    monkeypatch.setattr(ctrl._runner, "stream_task", fake_stream_task)
    monkeypatch.setattr(ctrl._runner, "stream_steer_mission", fake_stream_steer)
    return calls


@pytest.mark.parametrize(
    ("status", "goal", "extra_payload", "expect_dispatch"),
    [
        (
            TaskStatus.COMPLETED.value,
            P0_STEER,
            {"meta": {"resend": True}},
            "new_turn",
        ),
        (
            TaskStatus.COMPLETED.value,
            P0_STEER,
            {},
            "new_turn",
        ),
        (
            TaskStatus.MISSION_RUNNING.value,
            P0_STEER,
            {},
            "steer",
        ),
    ],
)
def test_handle_message_status_event_dispatch_matrix(
    base_state,
    monkeypatch,
    status,
    goal,
    extra_payload,
    expect_dispatch,
):
    """Fix 2 + Fix 5: L0 dispatch matches (status × event) — never done: FAILED."""
    state = (
        _completed_mission_state(base_state)
        if status == TaskStatus.COMPLETED.value
        else _running_mission_state(base_state)
    )
    get_state_store().save(state)

    ctrl = SessionController()
    calls = _track_dispatch(monkeypatch, ctrl)
    events = list(ctrl.handle_message(state["task_id"], goal, **extra_payload))

    assert not any('"status": "FAILED"' in e or "FAILED" in e.split("done")[-1] for e in events)
    if expect_dispatch == "new_turn":
        assert calls["stream_task"] == 1
        assert calls["stream_steer"] == 0
    else:
        assert calls["stream_steer"] == 1
        assert calls["stream_task"] == 0


def test_completed_interrupt_skips_cancel_and_steer(base_state, monkeypatch):
    """Fix 2: COMPLETED heuristic interrupt must not enter supersede steer path."""
    state = _completed_mission_state(base_state)
    get_state_store().save(state)

    cancel_called: list[str] = []

    def fake_cancel(st, *, reason: str = ""):
        cancel_called.append(reason)
        return st

    ctrl = SessionController()
    calls = _track_dispatch(monkeypatch, ctrl)
    monkeypatch.setattr("app.services.session_controller.RunController.cancel", fake_cancel)

    list(ctrl.handle_message(state["task_id"], P0_STEER))

    assert calls["stream_task"] == 1
    assert calls["stream_steer"] == 0
    assert cancel_called == []


def test_running_interrupt_cancels_and_steers(base_state, monkeypatch):
    """MISSION_RUNNING + P0 → cancel + stream_steer_mission (in-flight supersede)."""
    state = _running_mission_state(base_state)
    get_state_store().save(state)

    cancel_called: list[str] = []

    def fake_cancel(st, *, reason: str = ""):
        cancel_called.append(reason)
        return merge_state(st, execution_run={"run_id": "r1", "cancelled": True})

    ctrl = SessionController()
    calls = _track_dispatch(monkeypatch, ctrl)
    monkeypatch.setattr("app.services.session_controller.RunController.cancel", fake_cancel)

    list(ctrl.handle_message(state["task_id"], P0_STEER))

    assert calls["stream_steer"] == 1
    assert calls["stream_task"] == 0
    assert cancel_called == ["user_interrupt"]
