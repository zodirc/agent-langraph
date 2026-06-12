"""Thin execution profile — contract-driven reasoning tier, not canned text."""

from __future__ import annotations

from app.runtime.state import TaskStatus, merge_state
from app.services.event_classification import classify_user_event
from app.services.interaction_goal import (
    goal_is_capability_inquiry,
    goal_is_mission_status_query,
)
from app.services.intent_observation_policy import decide_intent_observation_policy
from app.services.thin_execution import reasoning_llm_purpose


def test_capability_inquiry_not_mission_status():
    assert goal_is_capability_inquiry("你能做什么")
    assert not goal_is_mission_status_query("你能做什么")
    assert goal_is_mission_status_query("你正在做什么")


def test_neng_zuo_shenme_classified_new_task_not_status_query(base_state):
    result = classify_user_event(base_state, payload={"goal": "你能做什么"})
    assert result.event_type == "new_task"


def test_turn1_invokes_intent_observation_for_interaction_goal(base_state):
    decision = decide_intent_observation_policy(
        base_state,
        explicit_mode="auto",
        route_audit_seed={"inferred_kind": "qa", "kind_confidence": 0.8},
    )
    assert decision.invoke_model
    assert decision.reason in ("turn1_interaction_goal", "interaction_mode_auto")


def test_thin_profile_uses_routing_purpose(base_state):
    state = merge_state(
        base_state,
        input_payload={**base_state["input_payload"], "thin_execution_profile": "qa_direct"},
    )
    assert reasoning_llm_purpose(state) == "routing"


def test_reasoning_node_uses_routing_purpose_for_thin_profile(base_state, monkeypatch):
    from app.nodes.reasoning_node import reasoning_node

    purposes: list[str] = []

    def _capture_invoke(purpose, *_args, **_kwargs):
        purposes.append(purpose)
        return {
            "summary": "我可以帮你完成问答、工程交付和写作任务。",
            "confidence": 0.9,
            "risk_level": "LOW",
            "structured": {},
        }

    state = merge_state(
        base_state,
        plan=["respond directly"],
        status=TaskStatus.PLANNED.value,
        input_payload={
            **base_state["input_payload"],
            "goal": "你能做什么",
            "target_mode": "qa_mode",
            "thin_execution_profile": "qa_direct",
            "pre_planning_completed": True,
        },
    )
    monkeypatch.setattr("app.nodes.reasoning_node.trace_enabled", lambda: False)
    monkeypatch.setattr("app.nodes.reasoning_node.answer_stream_enabled", lambda: False)
    monkeypatch.setattr("app.nodes.reasoning_node.invoke_structured", _capture_invoke)

    result = reasoning_node(state)
    assert purposes == ["routing"]
    assert "问答" in result["reasoning_result"]["summary"]
