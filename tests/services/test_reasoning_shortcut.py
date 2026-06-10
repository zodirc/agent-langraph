from app.runtime.state import TaskStatus, merge_state
from app.services.reasoning_shortcut import (
    clear_turn_carryover,
    should_use_execution_summary,
    summary_from_turn_execution,
    turn_had_execution,
)
from app.services.fact_layer import build_turn_facts


def _state(**kwargs):
    base = {
        "task_id": "t-shortcut",
        "session_id": "s-shortcut",
        "session_turn": 15,
        "status": TaskStatus.PLANNED.value,
        "input_payload": {
            "goal": "你之前做过什么？",
            "skip_reasoning_after_tools": True,
            "route_audit": {
                "inferred_kind": "qa",
                "kind_confidence": 0.5,
                "aligned": True,
            },
        },
        "tool_results": None,
    }
    base.update(kwargs)
    return base


def test_clear_turn_carryover_resets_skip_flag():
    payload = {"skip_reasoning_after_tools": True, "goal": "x"}
    cleaned = clear_turn_carryover(payload)
    assert cleaned.get("skip_reasoning_after_tools") is False


def test_qa_turn_does_not_use_execution_summary_despite_stale_skip():
    state = _state()
    facts = build_turn_facts(state)
    assert turn_had_execution(state, facts) is False
    assert should_use_execution_summary(state, facts) is False


def test_summary_empty_when_no_writing_this_turn():
    state = _state()
    facts = build_turn_facts(state)
    text = summary_from_turn_execution(facts, state)
    assert text == ""


def test_writing_turn_may_use_execution_summary():
    state = merge_state(
        _state(),
        status=TaskStatus.TOOL_EXECUTED.value,
        tool_results=[{"tool": "write_text_artifact", "status": "ok", "result": {"path": "novel.txt"}}],
        input_payload={
            **_state()["input_payload"],
            "skip_reasoning_after_tools": True,
            "writing_intent": {"enabled": True, "action": "write_body"},
            "route_audit": {"inferred_kind": "writing", "kind_confidence": 0.6},
        },
    )
    facts = build_turn_facts(state)
    assert turn_had_execution(state, facts) is True
    text = summary_from_turn_execution(facts, state)
    assert "novel.txt" in text
    assert should_use_execution_summary(state, facts) is True
