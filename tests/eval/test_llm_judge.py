"""LLM judge unit tests."""

from __future__ import annotations

from tests.eval.llm_judge import judge_task_state


def test_rule_judge_when_model_disabled() -> None:
    state = {
        "final_answer": "A detailed answer about the topic.",
        "reasoning_result": {"confidence": 0.8},
        "errors": [],
        "status": "COMPLETED",
        "input_payload": {"goal": "explain"},
    }
    scores = judge_task_state(state, goal="explain")
    assert scores["overall"] >= 2.0
    assert scores["source"] == "rules"
