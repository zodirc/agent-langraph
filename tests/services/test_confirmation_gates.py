"""Tests for confirmation gate registry."""

from app.services.confirmation.gate_registry import GateContext, intent_gate_required, outcome_gate_required


def test_intent_gate_material_intervention():
    ctx = GateContext(
        planning_result={"mission_intervention": {"action": "rewrite_outline", "force": True}},
        payload={"steer_applied_at": "t", "mission": {"kind": "writing"}},
    )
    assert intent_gate_required(ctx) is True


def test_intent_gate_skips_when_confirmed():
    ctx = GateContext(
        planning_result={},
        payload={"steer_applied_at": "t", "steer_intent_confirmed": True},
    )
    assert intent_gate_required(ctx) is False


def test_intent_gate_skips_on_foreground_supersede_replan():
    ctx = GateContext(
        planning_result={
            "mission_intervention": {"action": "edit_plot", "force": True},
            "steer_intent_summary": "使用原电影人物",
        },
        payload={
            "steer_applied_at": "t",
            "foreground_replan_dispatch": True,
            "latest_steer_message": "使用原电影人物",
        },
        state={"interrupt_context": {"foreground_operation": {"kind": "supersede_with_input"}}},
    )
    assert intent_gate_required(ctx) is False


def test_outcome_gate_write_outline_with_steer():
    ctx = GateContext(
        planning_result={},
        payload={"steer_applied_at": "t", "steer_watch_outcome": True},
        state={"task_id": "t1", "progress": {}},
        completed_item={"id": "wi-1", "kind": "write_outline"},
        observation={"has_failures": False},
    )
    assert outcome_gate_required(ctx) is True


def test_outcome_gate_append_requires_delta():
    ctx = GateContext(
        planning_result={},
        payload={
            "steer_applied_at": "t",
            "writing_stopped_for_steer": True,
        },
        state={"task_id": "t1", "progress": {}},
        completed_item={"id": "wi-2", "kind": "append_chapter"},
        observation={"has_failures": False},
    )
    assert outcome_gate_required(ctx) is False

    ctx2 = GateContext(
        planning_result={},
        payload={
            "steer_applied_at": "t",
            "writing_stopped_for_steer": True,
        },
        state={
            "task_id": "t1",
            "progress": {"writing_step_delta": {"excerpt": "新增段落"}},
        },
        completed_item={"id": "wi-2", "kind": "append_chapter"},
        observation={"has_failures": False},
    )
    assert outcome_gate_required(ctx2) is True
