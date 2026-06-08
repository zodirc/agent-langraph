"""Intent observation domain and structural fallback tests."""

from unittest.mock import patch

from app.domain.intent_observation import IntentObservationResult
from app.runtime.state import create_initial_state, merge_state
from app.services.intent_observation import (
    build_structural_observation,
    observe_intent,
)
from app.services.intent_observation_eval import GOLDEN_CASES, run_structural_baseline
from app.services.intent_observation_policy import (
    decide_intent_observation_policy,
    mechanical_resume_allowed_by_observation,
)
from app.services.pre_planning import run_pre_planning_pipeline, seed_pre_planning_route_audit


def test_structural_observation_engineering():
    state = create_initial_state(
        task_id="io-1",
        input_payload={"goal": "写一个 C++ 可编译项目，落盘 main.cpp"},
    )
    seed = seed_pre_planning_route_audit(state)
    result = build_structural_observation(state, route_audit_seed=seed)
    assert result.source == "structural"
    assert result.target_mode in ("engineering_mode", "qa_mode", "manuscript_mode")
    assert 0.0 <= result.confidence <= 1.0
    assert result.trace_id


def test_policy_skips_pure_greeting():
    state = create_initial_state(task_id="io-greet", input_payload={"goal": "你好"})
    seed = seed_pre_planning_route_audit(state)
    decision = decide_intent_observation_policy(
        state, explicit_mode=None, route_audit_seed=seed
    )
    assert decision.invoke_model is False
    assert decision.reason == "pure_greeting"


def test_policy_skips_explicit_engineering():
    state = create_initial_state(
        task_id="io-2",
        input_payload={
            "goal": "用 Python 写 CLI todo，落盘可运行",
            "interaction_mode": "engineering",
        },
    )
    seed = seed_pre_planning_route_audit(state)
    decision = decide_intent_observation_policy(
        state, explicit_mode="engineering", route_audit_seed=seed
    )
    assert decision.invoke_model is False
    assert decision.skip_reason


def test_policy_requires_auto_mode():
    state = create_initial_state(
        task_id="io-3",
        input_payload={"goal": "解释一下这个 bug，然后顺手修掉", "interaction_mode": "auto"},
    )
    seed = seed_pre_planning_route_audit(state)
    decision = decide_intent_observation_policy(
        state, explicit_mode="auto", route_audit_seed=seed
    )
    assert decision.invoke_model is True


def test_mechanical_resume_blocked_by_steer_replan_observation():
    state = create_initial_state(
        task_id="io-4",
        input_payload={"goal": "继续，但先把上一章节奏收紧"},
    )
    state = {
        **state,
        "intent_observation": {
            "turn_kind_candidate": "steer_replan",
            "confidence": 0.9,
            "source": "llm",
        },
    }
    assert mechanical_resume_allowed_by_observation(state) is False


@patch("app.services.intent_observation._invoke_observation_model")
def test_observe_intent_structural_when_policy_skips(mock_llm):
    state = create_initial_state(
        task_id="io-5",
        input_payload={
            "goal": "用 Python 写 CLI todo",
            "interaction_mode": "engineering",
        },
    )
    seed = seed_pre_planning_route_audit(state)
    result = observe_intent(state, explicit_mode="engineering", route_audit_seed=seed)
    mock_llm.assert_not_called()
    assert result.source == "structural"


def test_pre_planning_writes_intent_observation():
    state = create_initial_state(
        task_id="io-6",
        input_payload={
            "goal": "做一个浏览器 2048 小游戏",
            "interaction_mode": "engineering",
        },
    )
    out = run_pre_planning_pipeline(state)
    assert out.get("intent_observation")
    payload = out.get("input_payload") or {}
    assert payload.get("route_audit", {}).get("intent_observation_summary")
    assert payload.get("pre_planning_completed") is True


def test_intent_observation_result_roundtrip():
    original = IntentObservationResult(
        intent_kind="engineering",
        target_mode="engineering_mode",
        session_relation="stay",
        confidence=0.88,
        reasons=["test"],
        trace_id="io-test",
    )
    restored = IntentObservationResult.from_dict(original.to_dict())
    assert restored.intent_kind == "engineering"
    assert restored.confidence == 0.88


def test_policy_invokes_model_on_foreground_preempt_replan():
    state = merge_state(
        create_initial_state(task_id="io-preempt"),
        input_payload={
            "goal": "不能出现架空人物",
            "foreground_preempt_consumed": True,
            "steer_replan_mode": "rewrite",
            "require_planning_after_steer": True,
            "mission": {"kind": "writing"},
        },
    )
    seed = seed_pre_planning_route_audit(state)
    decision = decide_intent_observation_policy(state, explicit_mode=None, route_audit_seed=seed)
    assert decision.invoke_model is True
    assert decision.reason == "foreground_preempt_replan"


def test_structural_observation_marks_steer_replan_after_preempt():
    state = merge_state(
        create_initial_state(task_id="io-preempt-struct"),
        input_payload={
            "foreground_preempt_consumed": True,
            "steer_replan_mode": "repair",
            "mission": {"kind": "writing"},
        },
    )
    seed = seed_pre_planning_route_audit(state)
    result = build_structural_observation(state, route_audit_seed=seed)
    assert result.turn_kind_candidate == "steer_replan"
    assert result.needs_planning is True


def test_structural_golden_baseline_runs():
    from app.services.intent_observation_eval import ALL_GOLDEN_CASES

    report = run_structural_baseline()
    assert report["total"] == len(ALL_GOLDEN_CASES)
    assert report["passed"] >= 1
