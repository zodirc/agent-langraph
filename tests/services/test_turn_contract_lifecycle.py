from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.mission_execution import issue_execution_grant_to_payload
from app.services.mission_steer import apply_steer_planning_gate, steer_requires_planning
from app.services.progress_evaluator import evaluate_mission_control
from app.services.turn_contract import (
    apply_turn_contract_to_payload,
    contract_tool_names,
    finalize_turn_execution_plan,
    planning_fallback_from_state,
)
from app.services.turn_contract_lifecycle import (
    REASON_NON_RECOVERABLE_FAILURE,
    apply_non_recoverable_failure_lifecycle,
    contract_conflicts_with_writing_intent,
    contract_replan_required,
    invalidate_turn_contract_payload,
    reconcile_turn_contract_execution,
    sanitize_turn_contract,
)


def _writing_mission_state(**extra):
    base = create_initial_state(
        input_payload={
            "goal": "太平天国长篇",
            "mission": {
                "kind": "writing",
                "step_policy": {
                    "first_step": "outline",
                    "then": "append_body",
                    "outline_artifact": "outline.txt",
                    "body_artifact": "novel.txt",
                },
            },
        }
    )
    mission = {
        "kind": "writing",
        "step_policy": {
            "first_step": "outline",
            "then": "append_body",
            "outline_artifact": "outline.txt",
            "body_artifact": "novel.txt",
        },
    }
    return merge_state(
        base,
        mission=mission,
        manuscript={"task_id": base["task_id"], "outline_bytes": 0, "body_bytes": 0},
        **extra,
    )


def test_invalidate_on_steer_gate():
    payload = apply_turn_contract_to_payload(
        {"goal": "x"},
        {
            "intent_kind": "reasoning_only",
            "primary_op": "reasoning",
            "tools": ["read_text_artifact"],
            "forbid": ["write_outline"],
        },
    )
    payload["selected_tools"] = ["read_text_artifact"]
    out = apply_steer_planning_gate(payload)
    assert out.get("turn_contract") is None
    assert contract_replan_required(out)
    assert steer_requires_planning(out)


def test_invalidate_on_execution_grant():
    payload = apply_turn_contract_to_payload(
        {"goal": "x"},
        {
            "intent_kind": "forward_write",
            "primary_op": "write_outline",
            "tools": [],
            "forbid": [],
        },
    )
    out = issue_execution_grant_to_payload(payload, source="resume")
    assert out.get("turn_contract") is None
    assert contract_replan_required(out)
    assert out.get("execution_grant")


def test_conflict_detects_read_only_before_outline():
    state = _writing_mission_state(
        input_payload={
            "goal": "x",
            "writing_intent": {
                "enabled": True,
                "action": "write_outline",
            },
            **apply_turn_contract_to_payload(
                {},
                {
                    "intent_kind": "reasoning_only",
                    "primary_op": "reasoning",
                    "tools": ["read_text_artifact"],
                    "forbid": ["write_outline", "append_body"],
                },
            ),
        }
    )
    conflict = contract_conflicts_with_writing_intent(state["input_payload"], state)
    assert conflict is not None


def test_sanitize_repairs_forbidden_write_outline():
    state = _writing_mission_state(
        input_payload={
            "goal": "x",
            "writing_intent": {"enabled": True, "action": "write_outline"},
        }
    )
    bad = {
        "intent_kind": "reasoning_only",
        "primary_op": "reasoning",
        "tools": ["read_text_artifact"],
        "forbid": ["write_outline"],
    }
    fixed = sanitize_turn_contract(bad, state["input_payload"], state)
    assert fixed["primary_op"] == "write_outline"
    assert "write_outline" not in (fixed.get("forbid") or [])
    assert fixed.get("tools") == []


def test_reconcile_clears_stale_contract_before_writing_subgraph():
    state = _writing_mission_state(
        input_payload={
            "goal": "x",
            "writing_intent": {"enabled": True, "action": "write_outline"},
            **apply_turn_contract_to_payload(
                {},
                {
                    "intent_kind": "reasoning_only",
                    "primary_op": "reasoning",
                    "tools": ["read_text_artifact"],
                    "forbid": ["write_outline"],
                },
            ),
        },
        progress={
            "work_plan": {
                "mode": "lazy",
                "items": [
                    {
                        "id": "w1",
                        "kind": "plan_step",
                        "title": "outline via writing",
                        "status": "running",
                    }
                ],
            }
        },
    )
    reconciled = reconcile_turn_contract_execution(state)
    assert reconciled["input_payload"].get("turn_contract") is None
    assert contract_replan_required(reconciled["input_payload"])


def test_non_recoverable_failure_resets_streak_and_requires_replan():
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
        progress={"consecutive_failures": 4},
        mission={"kind": "writing", "budget": {"max_failures": 5}},
        observation={"has_failures": True},
    )
    state = apply_non_recoverable_failure_lifecycle(state)
    assert contract_replan_required(state["input_payload"])
    assert int(state["progress"]["consecutive_failures"]) == 0
    assert state["errors"] == [
        "tool_execution(non_retryable): Artifact not found: output.md"
    ]
    inv = state["input_payload"]["turn_contract_invalidation"]
    assert inv["reason"] == REASON_NON_RECOVERABLE_FAILURE

    result = evaluate_mission_control(state)
    assert result.done is False
    assert "replan" in result.reason


def test_finalize_sanitizes_llm_read_only_contract(base_state):
    mission = {
        "kind": "writing",
        "step_policy": {
            "first_step": "outline",
            "then": "append_body",
            "outline_artifact": "outline.txt",
        },
    }
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"outline_bytes": 0, "body_bytes": 0},
    )
    result = {
        "selected_tools": ["read_text_artifact"],
        "writing_intent": {"enabled": False},
        "plan": ["read outline"],
        "skip_retrieval": True,
    }
    payload, tools = finalize_turn_execution_plan(
        result,
        dict(state["input_payload"]),
        state,
        [],
        mission=mission,
        steer_planning_turn=False,
    )
    contract = payload.get("turn_contract") or {}
    assert contract.get("primary_op") == "write_outline"
    assert "read_text_artifact" not in (tools or [])
    assert "read_text_artifact" not in (payload.get("selected_tools") or [])


def test_apply_turn_contract_clears_stale_selected_tools():
    payload = {
        "goal": "x",
        "selected_tools": ["read_text_artifact"],
        "writing_intent": {"enabled": True, "action": "write_outline"},
    }
    contract = {
        "intent_kind": "forward_write",
        "primary_op": "write_outline",
        "tools": [],
        "forbid": [],
    }
    out = apply_turn_contract_to_payload(payload, contract)
    assert out.get("selected_tools") is None or out.get("selected_tools") == []
    assert contract_tool_names(out) == []


def test_planning_fallback_on_contract_replan_outline_pending():
    state = _writing_mission_state(
        input_payload=invalidate_turn_contract_payload(
            {"goal": "太平天国长篇"},
            REASON_NON_RECOVERABLE_FAILURE,
        ),
    )
    state = merge_state(
        state,
        progress={
            "work_plan": {
                "items": [
                    {
                        "id": "w1",
                        "kind": "plan_step",
                        "title": "outline via writing",
                        "status": "running",
                    }
                ]
            }
        },
    )
    fb = planning_fallback_from_state(state)
    assert fb is not None
    assert fb["writing_intent"]["action"] == "write_outline"
    assert fb.get("selected_tools") == []


def test_invalidate_payload_idempotent_signature():
    payload = invalidate_turn_contract_payload({}, REASON_NON_RECOVERABLE_FAILURE)
    state = merge_state(
        create_initial_state(input_payload=payload),
        progress={"consecutive_failures": 2},
        errors=["tool_execution(non_retryable): x"],
        status=TaskStatus.TOOL_FAILED.value,
    )
    once = apply_non_recoverable_failure_lifecycle(state)
    twice = apply_non_recoverable_failure_lifecycle(once)
    assert twice is once
