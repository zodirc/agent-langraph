"""Narrate/QA answer-ready shortcuts (post-reasoning replan + early delivered)."""

from __future__ import annotations

from app.nodes.reasoning_or_writing_node import route_after_reasoning_or_writing
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.graph_runner import _should_emit_early_delivered
from app.services.pre_planning import planning_must_run_llm, should_skip_qa_planning_llm
from app.services.pre_planning import run_pre_planning_pipeline
from app.services.turn_kind import is_narrate_answer_turn_ready


def _narrate_answer_state(**overrides):
    state = merge_state(
        create_initial_state(
            input_payload={
                "goal": "你好",
                "turn_kind": "narrate_only",
                "target_mode": "qa_mode",
            },
        ),
        status=TaskStatus.REASONED.value,
        reasoning_result={"summary": "你好！有什么可以帮你的？"},
        planned_actions=[{"type": "answer", "params": {}, "completes_turn": True}],
    )
    return merge_state(state, **overrides)


def test_is_narrate_answer_turn_ready_for_side_qa():
    state = _narrate_answer_state()
    assert is_narrate_answer_turn_ready(state) is True


def test_is_narrate_answer_turn_ready_false_when_contract_needs_executor():
    state = _narrate_answer_state(
        input_payload={
            "goal": "写文件",
            "turn_contract": {"primary_op": "write_artifact", "filename": "a.txt"},
        },
    )
    assert is_narrate_answer_turn_ready(state) is False


def test_route_after_reasoning_skips_replan_when_answer_ready(isolated_stores):
    state = _narrate_answer_state(
        input_payload={
            "goal": "你好",
            "turn_kind": "narrate_only",
            "route_audit_replan": True,
            "planning_revision_count": 1,
        },
    )
    assert route_after_reasoning_or_writing(state) == "verification"


def test_planning_must_run_llm_false_for_narrate_replan():
    state = _narrate_answer_state(
        input_payload={
            "goal": "你好",
            "turn_kind": "narrate_only",
            "route_audit_replan": True,
            "planning_revision_count": 1,
        },
    )
    assert planning_must_run_llm(state) is False


def test_planning_must_run_llm_true_for_execute_replan():
    state = merge_state(
        create_initial_state(
            input_payload={
                "goal": "编辑 outline.txt",
                "turn_contract": {"primary_op": "edit_artifact", "filename": "outline.txt"},
                "route_audit_replan": True,
            },
        ),
        planning_revision_count=1,
    )
    assert planning_must_run_llm(state) is True


def test_qa_replan_stays_on_thin_planning():
    state = create_initial_state(
        input_payload={"goal": "你好", "route_audit_replan": True},
    )
    state = run_pre_planning_pipeline(state)
    assert planning_must_run_llm(state) is False
    assert should_skip_qa_planning_llm(state) is True


def test_should_emit_early_delivered_for_narrate_answer():
    state = _narrate_answer_state()
    assert _should_emit_early_delivered(state) is True


def test_should_not_emit_early_delivered_when_rejected():
    state = _narrate_answer_state(status=TaskStatus.REJECTED.value)
    assert _should_emit_early_delivered(state) is False
