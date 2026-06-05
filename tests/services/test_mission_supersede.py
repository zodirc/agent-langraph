"""Foreground supersede vs pure resume."""

import pytest

from app.runtime.state import TaskStatus, merge_state
from app.services.graph_runner import GraphRunner
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import apply_steer_planning_gate
from app.services.mission_supersede import (
    FG_STATUS_QUEUED,
    FOREGROUND_KIND_SUPERSEDE,
    foreground_operation,
    mark_supersede_replan_queued,
    prepare_supersede_replan_payload,
)
from app.services.state_store import get_state_store


def test_mark_supersede_replan_queued_bumps_revision(base_state):
    state = merge_state(
        base_state,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload=apply_steer_planning_gate({"goal": "写同人", "intent_revision": 1}),
    )
    out = mark_supersede_replan_queued(state, source="test")
    payload = out.get("input_payload") or {}
    assert int(payload.get("intent_revision") or 0) >= 2
    op = foreground_operation(out.get("interrupt_context") or {})
    assert op.get("kind") == FOREGROUND_KIND_SUPERSEDE
    assert op.get("status") == FG_STATUS_QUEUED
    assert out.get("status") == TaskStatus.MISSION_PAUSED.value


def test_prepare_resume_rejects_supersede_pending(base_state, monkeypatch):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload=apply_steer_planning_gate({"goal": "写同人"}),
    )
    state = mark_supersede_replan_queued(state)
    get_state_store().save(state)

    runner = GraphRunner()
    with pytest.raises(ValueError, match="supersede"):
        runner.prepare_resume_mission(state["task_id"])


def test_prepare_supersede_replan_sets_dispatch_flag(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload=apply_steer_planning_gate({"goal": "写同人"}),
    )
    state = mark_supersede_replan_queued(state)
    get_state_store().save(state)

    runner = GraphRunner()
    prepared = runner.prepare_supersede_replan(state["task_id"])
    payload = prepared.get("input_payload") or {}
    assert payload.get("foreground_replan_dispatch") is True
    assert "execution_grant" not in payload
    op = foreground_operation(prepared.get("interrupt_context") or {})
    assert op.get("status") == "replan_dispatching"


def test_prepare_supersede_replan_payload_clears_grant():
    out = prepare_supersede_replan_payload(
        {"execution_grant": {"issued_at": "x"}, "steer_replan_resume": True}
    )
    assert "execution_grant" not in out
    assert "steer_replan_resume" not in out
    assert out.get("foreground_replan_dispatch") is True
