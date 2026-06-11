"""LLM-free golden tests for the single convergence gate (unified-core WP-2)."""

from __future__ import annotations

from app.services.converge import (
    NEXT_FINALIZE,
    NEXT_FORCE_WRITE,
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


def test_read_loop_write_budget_exhausted_finalizes():
    reads = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        for _ in range(READ_LOOP_THRESHOLD)
    ]
    failed_writes = [
        {"tool": "write_text_artifact", "status": "error", "error": "generation failed"}
        for _ in range(4)
    ]
    state = _base_state(
        tool_results=reads + failed_writes,
        input_payload={
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
            "effective_mode_contract": {"execution": {"max_write_actions": 4}},
        },
    )
    result = evaluate_convergence(state)
    assert result.next == NEXT_FINALIZE
    assert result.reason == "write_budget_exhausted"


def test_edit_not_applied_writing_forces_write():
    state = _base_state(
        tool_results=[_edit_result(0)],
        turn_facts={"edits_attempted": 1, "edits_applied": 0},
        input_payload={
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
        },
    )
    result = evaluate_convergence(state)
    assert result.next == NEXT_FORCE_WRITE
    assert result.reason == "edit_not_applied_force_write"


def test_read_loop_with_failed_edit_counts_as_read_loop():
    items = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        for _ in range(READ_LOOP_THRESHOLD)
    ] + [_edit_result(0)]
    state = _base_state(
        tool_results=items,
        input_payload={
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
        },
    )
    result = evaluate_convergence(state)
    assert result.next == NEXT_FORCE_WRITE


def test_read_loop_with_writing_intent_forces_write_not_finalize():
    reads = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        for _ in range(READ_LOOP_THRESHOLD)
    ]
    state = _base_state(
        tool_results=reads,
        input_payload={
            "writing_intent": {"enabled": True, "source": "manuscript_mode"},
            "target_mode": "manuscript_mode",
            "goal": "重写小说，内容太少",
        },
    )
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_FORCE_WRITE
    assert result.reason == "read_loop_force_write"


def test_read_loop_with_pending_artifact_save_replans():
    reads = [
        {"tool": "read_text_artifact", "status": "ok", "result": {"status": "ok"}}
        for _ in range(READ_LOOP_THRESHOLD)
    ]
    state = _base_state(
        tool_results=reads,
        input_payload={"goal": "优化故事那一篇"},
        plan=["读取故事", "润色", "保存优化后的故事"],
    )
    result = evaluate_convergence(state)
    assert result.done is False
    assert result.next == NEXT_REPLAN
    assert result.reason == "artifact_edit_needs_write"


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


def test_failed_rm_after_successful_write_does_not_replan_forever():
    state = _base_state(
        planned_actions=[
            {
                "type": "write_artifact",
                "params": {"filename": "利迪策之刃_大纲.md", "content": "x"},
            },
            {
                "type": "run_tool",
                "params": {"name": "rm_path", "path": "深空余烬_大纲.md"},
            },
        ],
        tool_results=[
            {
                "tool": "write_text_artifact",
                "status": "ok",
                "result": {"filename": "利迪策之刃_大纲.md"},
            },
            {
                "tool": "rm_path",
                "status": "error",
                "error": "preview_token required: run dry_run first",
            },
        ],
        reasoning_result={"summary": "已写入新大纲文件。"},
    )
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.next == NEXT_PROCEED


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


def test_writing_answer_without_persist_forces_write():
    state = _base_state(
        reasoning_result={"summary": "已制定五步创作计划，准备开始撰写。"},
        input_payload={
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
        },
    )
    result = evaluate_convergence(state)
    assert result.next == NEXT_FORCE_WRITE
    assert result.reason == "writing_answer_without_persist"


def test_writing_explicit_ask_converges_answer_ready():
    state = _base_state(
        reasoning_result={
            "summary": "需先补充剧情信息或用户提供故事大纲，方可开始撰写小说正文。"
        },
        input_payload={
            "writing_intent": {"enabled": True},
            "target_mode": "manuscript_mode",
        },
    )
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "answer_ready"


def test_answer_plan_with_no_tools_converges_on_answer():
    state = _base_state(
        planned_actions=[{"type": "answer", "params": {}, "completes_turn": True}],
        reasoning_result={"summary": "答案"},
    )
    result = evaluate_convergence(state)
    assert result.done is True
    assert result.reason == "answer_ready"
