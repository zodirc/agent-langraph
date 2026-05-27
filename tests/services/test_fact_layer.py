from app.services.fact_layer import (
    apply_reasoning_guard,
    build_turn_facts,
    reasoning_context_from_state,
    validate_reasoning_summary,
)
from app.runtime.state import merge_state


def test_build_turn_facts_from_calculator(base_state):
    state = merge_state(
        base_state,
        selected_tools=["calculator"],
        tool_results=[
            {
                "tool": "calculator",
                "result": {"expression": "123+456", "result": "579", "status": "ok"},
            }
        ],
        status="TOOL_EXECUTED",
    )
    facts = build_turn_facts(state)
    assert facts["tool_count"] == 1
    assert facts["tools_executed"][0]["output"] == "579"
    assert "tool:calculator" in facts["executed_actions"]


def test_reasoning_context_uses_turn_facts_only(base_state):
    state = merge_state(
        base_state,
        turn_facts={"executed_actions": ["tool:calculator"], "tools_executed": []},
        tool_results=[{"tool": "calculator", "result": {"result": "579"}}],
        retrieved_knowledge=[{"text": "background"}],
    )
    ctx = reasoning_context_from_state(state)
    assert ctx["turn_facts"]["executed_actions"] == ["tool:calculator"]
    assert "instructions" in ctx
    assert ctx["retrieved_knowledge"] == [{"text": "background"}]


def test_validate_reasoning_future_write_warning():
    facts = {"executed_actions": [], "tools_executed": [], "manuscript": {}}
    warnings = validate_reasoning_summary("本轮将要追加一万字", facts)
    assert warnings


def test_apply_reasoning_guard_prepends_executed_lead(base_state):
    facts = build_turn_facts(
        merge_state(
            base_state,
            tool_results=[
                {"tool": "calculator", "result": {"result": "579", "expression": "1+2"}},
            ],
        )
    )
    out = apply_reasoning_guard(
        {"summary": "将要写很多内容", "confidence": 0.8, "structured": {}},
        facts,
    )
    assert "【本轮已执行】" in out["summary"]
    assert out["structured"]["fact_warnings"]
