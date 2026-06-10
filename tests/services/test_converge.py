"""LLM-free golden tests for the single convergence gate (unified-core WP-2)."""

from __future__ import annotations

from app.services.converge import (
    NEXT_FINALIZE,
    NEXT_PROCEED,
    NEXT_REPLAN,
    READ_LOOP_THRESHOLD,
    evaluate_convergence,
)


def _base_state(**overrides):
    state = {
        "task_id": "t-conv",
        "session_id": "s-conv",
        "user_id": "u",
        "task_type": "qa",
        "input_payload": {},
        "status": "TOOL_EXECUTED",
        "tool_results": [],
        "turn_facts": {},
        "node_history": [],
        "audit_log": [],
    }
    state.update(overrides)
    return state


def _edit_result(replacements: int):
    return {
        "tool": "edit_text_artifact",
        "status": "ok",
        "result": {"status": "ok", "replacements": replacements},
    }


def test_edit_applied_converges_done():
    state = _base_state(tool_results=[_edit_result(1)])
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.next == NEXT_PROCEED
    assert result.reason == "edit_applied"


def test_edit_zero_replacements_does_not_converge():
    state = _base_state(tool_results=[_edit_result(0)])
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_REPLAN
    assert result.reason == "edit_not_applied"


def test_edit_honesty_prefers_turn_facts_from_action_executor():
    state = _base_state(
        turn_facts={"edits_attempted": 2, "edits_applied": 1, "edit_applied": False}
    )
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_REPLAN


def test_read_loop_triggers_safe_finalize():
    reads = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        for _ in range(READ_LOOP_THRESHOLD)
    ]
    state = _base_state(tool_results=reads)
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_FINALIZE
    assert result.reason == "read_loop"


def test_reads_followed_by_side_effect_is_not_a_loop():
    items = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        for _ in range(READ_LOOP_THRESHOLD)
    ] + [
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"status": "ok", "bytes": 10},
        }
    ]
    state = _base_state(tool_results=items)
    result = evaluate_convergence(state)
    assert result.reason != "read_loop"


def test_contract_fulfilled_converges_done():
    state = _base_state(
        input_payload={"turn_contract": {"primary_op": "run_tools", "tools": ["echo"]}},
        tool_results=[{"tool": "echo", "status": "ok", "result": {"echo": "hi", "status": "ok"}}],
    )
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "contract_fulfilled"


def test_contract_unfulfilled_requests_replan():
    state = _base_state(
        input_payload={"turn_contract": {"primary_op": "run_tools", "tools": ["echo"]}},
        tool_results=[],
    )
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_REPLAN
    assert result.reason == "contract_unfulfilled"


def test_answer_nonempty_converges_done():
    state = _base_state(reasoning_result={"summary": "最终回答。"})
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "answer_ready"


def test_final_answer_fallback_converges_done():
    state = _base_state(final_answer="answer text")
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "answer_ready"


def test_no_signal_keeps_pipeline_flowing_without_replan():
    state = _base_state()
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_PROCEED
    assert result.reason == "no_signal"


def test_pending_planned_side_effect_action_requests_replan():
    state = _base_state(
        planned_actions=[
            {"type": "read_artifact", "params": {"filename": "outline.txt"}},
            {
                "type": "edit_artifact",
                "params": {"filename": "outline.txt", "old_text": "a", "new_text": "b"},
            },
        ],
        tool_results=[
            {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        ],
    )
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_REPLAN
    assert "actions_pending" in result.reason


def test_exploration_only_plan_returns_to_planning_after_reads():
    state = _base_state(
        planned_actions=[{"type": "read_artifact", "params": {"filename": "outline.txt"}}],
        tool_results=[
            {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        ],
    )
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_REPLAN
    assert result.reason == "exploration_needs_replan"


def test_executed_planned_actions_converge_done():
    state = _base_state(
        planned_actions=[
            {
                "type": "edit_artifact",
                "params": {"filename": "outline.txt", "old_text": "a", "new_text": "b"},
                "completes_turn": True,
            }
        ],
        tool_results=[_edit_result(1)],
    )
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "edit_applied"


def test_answer_plan_with_no_tools_converges_on_answer():
    state = _base_state(
        planned_actions=[{"type": "answer", "params": {}, "completes_turn": True}],
        reasoning_result={"summary": "答案"},
    )
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "answer_ready"
