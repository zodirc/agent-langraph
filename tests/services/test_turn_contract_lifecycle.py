"""Generic turn-contract lifecycle (unified-core WP-4)."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.turn_contract_lifecycle import (
    REASON_NON_RECOVERABLE_FAILURE,
    REASON_STEER,
    apply_non_recoverable_failure_lifecycle,
    clear_contract_replan_requirement,
    contract_replan_required,
    invalidate_turn_contract_payload,
)


def test_invalidate_retires_contract_and_tool_routing():
    payload = {
        "goal": "x",
        "turn_contract": {"primary_op": "edit_artifact", "tools": ["edit_text_artifact"]},
        "selected_tools": ["edit_text_artifact"],
        "tool_stages": [["edit_text_artifact"]],
    }
    out = invalidate_turn_contract_payload(payload, REASON_STEER)
    assert out["turn_contract"] is None
    assert out["selected_tools"] is None
    assert out["tool_stages"] is None
    assert contract_replan_required(out)
    assert out["turn_contract_invalidation"]["reason"] == REASON_STEER
    assert out["turn_contract_invalidation"]["had_contract"] is True


def test_clear_replan_requirement():
    payload = invalidate_turn_contract_payload({"goal": "x"}, REASON_STEER)
    cleared = clear_contract_replan_requirement(payload)
    assert not contract_replan_required(cleared)
    assert "turn_contract_invalidation" not in cleared


def test_non_recoverable_failure_requires_replan():
    state = merge_state(
        create_initial_state(input_payload={"goal": "novel"}),
        status=TaskStatus.TOOL_FAILED.value,
        errors=["tool_execution(non_retryable): Artifact not found: output.md"],
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "error",
                "error": "Artifact not found: output.md",
                "non_retryable": True,
            }
        ],
        observation={"has_failures": True},
    )
    state = apply_non_recoverable_failure_lifecycle(state)
    assert contract_replan_required(state["input_payload"])
    assert state["errors"] == [
        "tool_execution(non_retryable): Artifact not found: output.md"
    ]
    inv = state["input_payload"]["turn_contract_invalidation"]
    assert inv["reason"] == REASON_NON_RECOVERABLE_FAILURE


def test_invalidate_payload_idempotent_signature():
    payload = invalidate_turn_contract_payload({}, REASON_NON_RECOVERABLE_FAILURE)
    state = merge_state(
        create_initial_state(input_payload=payload),
        errors=["tool_execution(non_retryable): x"],
        status=TaskStatus.TOOL_FAILED.value,
    )
    once = apply_non_recoverable_failure_lifecycle(state)
    twice = apply_non_recoverable_failure_lifecycle(once)
    assert twice is once
