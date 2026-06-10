"""Generic turn contract (unified-core WP-4): Action vocabulary only."""

from app.runtime.state import merge_state
from app.services.turn_contract import (
    build_turn_contract,
    contract_requires_side_effects,
    contract_tool_names,
    is_turn_contract_fulfilled,
    validate_turn_contract_execution,
)


def test_build_turn_contract_from_explicit_block():
    contract = build_turn_contract(
        {"turn_contract": {"primary_op": "run_code", "tools": [], "forbid": []}},
        {},
    )
    assert contract["primary_op"] == "run_code"


def test_build_turn_contract_derives_from_actions():
    contract = build_turn_contract(
        {
            "actions": [
                {"type": "read_artifact", "params": {"filename": "a.txt"}},
                {"type": "edit_artifact", "params": {"filename": "a.txt"}},
            ]
        },
        {},
    )
    assert contract["primary_op"] == "edit_artifact"
    assert contract["tools"] == ["read_text_artifact", "edit_text_artifact"]


def test_build_turn_contract_answer_only():
    contract = build_turn_contract({"actions": [{"type": "answer"}]}, {})
    assert contract["primary_op"] == "answer"
    assert contract["tools"] == []
    assert not contract_requires_side_effects({"turn_contract": contract})


def test_build_turn_contract_run_tool_collects_tool_name():
    contract = build_turn_contract(
        {"actions": [{"type": "run_tool", "params": {"name": "calculator", "expression": "1+1"}}]},
        {},
    )
    assert contract["primary_op"] == "run_tool"
    assert contract["tools"] == ["calculator"]


def test_side_effect_contract_requires_side_effects():
    payload = {"turn_contract": {"primary_op": "edit_artifact", "tools": ["edit_text_artifact"]}}
    assert contract_requires_side_effects(payload)


def test_legacy_primary_with_tools_requires_side_effects():
    payload = {"turn_contract": {"primary_op": "edit_plot", "tools": ["edit_text_artifact"]}}
    assert contract_requires_side_effects(payload)


def test_contract_tool_names_falls_back_to_selected_tools():
    assert contract_tool_names({"selected_tools": ["calculator"]}) == ["calculator"]


def test_validate_reports_missing_tool(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {"primary_op": "edit_artifact", "tools": ["edit_text_artifact"]},
        },
        tool_results=[],
    )
    issues = validate_turn_contract_execution(state)
    assert "contract_expected_tool_missing:edit_text_artifact" in issues
    assert "contract_primary_op_unfulfilled:edit_artifact" in issues
    assert not is_turn_contract_fulfilled(state)


def test_validate_fulfilled_after_execution(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {"primary_op": "edit_artifact", "tools": ["edit_text_artifact"]},
        },
        tool_results=[{"tool": "edit_text_artifact", "status": "ok", "result": {"replacements": 2}}],
        turn_facts={"edit_applied": True},
    )
    assert validate_turn_contract_execution(state) == []
    assert is_turn_contract_fulfilled(state)


def test_validate_flags_zero_replacement_edit(base_state):
    """Edit honesty: tool ran but applied nothing → contract not fulfilled."""
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {"primary_op": "edit_artifact", "tools": ["edit_text_artifact"]},
        },
        tool_results=[{"tool": "edit_text_artifact", "status": "ok", "result": {"replacements": 0}}],
        turn_facts={"edit_applied": False},
    )
    assert "contract_edit_not_applied" in validate_turn_contract_execution(state)
    assert not is_turn_contract_fulfilled(state)


def test_no_contract_is_always_fulfilled(base_state):
    assert is_turn_contract_fulfilled(base_state)
