"""Session source material inquiry — LLM intent observation + thin planning."""

from unittest.mock import patch

import pytest

from app.domain.intent_observation import IntentObservationResult
from app.nodes.planning_node import planning_node
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.event_classification import classify_user_event
from app.services.interaction_goal import goal_is_session_source_inquiry
from app.services.pre_planning import (
    run_pre_planning_pipeline,
    seed_pre_planning_route_audit,
    should_skip_session_source_inquiry_planning,
)
from app.runtime.planning_gate_router import route_after_incremental_planning
from app.services.intent_observation_policy import decide_intent_observation_policy


def test_policy_turn1_session_source_requires_llm():
    state = create_initial_state(
        task_id="io-session-src",
        input_payload={
            "goal": "你阅读理解过我们的素材了么",
            "interaction_mode": "writing",
        },
    )
    seed = seed_pre_planning_route_audit(state)
    decision = decide_intent_observation_policy(
        state, explicit_mode="writing", route_audit_seed=seed
    )
    assert decision.invoke_model is True
    assert decision.reason in ("turn1_interaction_goal", "explicit_mode_disambiguation")


@pytest.fixture
def regex_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.services.interaction_goal._session_source_regex_fallback_enabled",
        lambda: True,
    )


def test_goal_from_intent_observation_llm():
    state = {
        "intent_observation": {
            "interaction_goal": "session_source_inquiry",
            "confidence": 0.92,
        }
    }
    assert goal_is_session_source_inquiry("你是否了解了我们提供给你的素材", state) is True


def test_goal_rejects_llm_delivery_goal():
    state = {
        "intent_observation": {
            "interaction_goal": "delivery",
            "confidence": 0.9,
        }
    }
    assert goal_is_session_source_inquiry("你是否了解了我们提供给你的素材", state) is False


def test_goal_without_observation_and_no_regex_fallback():
    assert goal_is_session_source_inquiry("你看过我们的素材了么") is False


def test_goal_regex_fallback_legacy(regex_fallback):
    assert goal_is_session_source_inquiry("你看过我们的素材了么") is True
    assert goal_is_session_source_inquiry("你是否了解了我们提供给你的素材") is False


def test_classify_session_source_inquiry_as_clarification(regex_fallback):
    state = create_initial_state(input_payload={"goal": "写小说"})
    result = classify_user_event(
        state,
        payload={"goal": "你看过我们的素材了么"},
    )
    assert result.event_type == "clarification"
    assert result.source == "session_source_inquiry"


@patch("app.services.intent_observation._invoke_observation_model")
def test_pre_planning_llm_session_source_thin_planning(mock_llm, isolated_stores):
    mock_llm.return_value = IntentObservationResult(
        source="llm",
        intent_kind="qa",
        target_mode="qa_mode",
        session_relation="stay",
        interaction_goal="session_source_inquiry",
        turn_kind_candidate="narrate_only",
        confidence=0.93,
        reasons=["meta_question_about_imported_material"],
    )
    state = create_initial_state(
        task_id="session-source-llm-intent",
        input_payload={
            "goal": "你是否了解了我们提供给你的素材",
            "interaction_mode": "writing",
        },
    )
    state = run_pre_planning_pipeline(state)
    assert should_skip_session_source_inquiry_planning(state) is True
    assert (state.get("input_payload") or {}).get("interaction_goal") == "session_source_inquiry"


@patch("app.services.llm_client.invoke_structured")
@patch("app.services.intent_observation._invoke_observation_model")
def test_planning_session_source_via_llm_intent(
    mock_obs_llm, mock_planning_llm, isolated_stores
):
    mock_obs_llm.return_value = IntentObservationResult(
        source="llm",
        intent_kind="qa",
        target_mode="qa_mode",
        interaction_goal="session_source_inquiry",
        turn_kind_candidate="narrate_only",
        confidence=0.91,
    )
    mock_planning_llm.side_effect = AssertionError("full planning LLM must not run")
    state = create_initial_state(
        task_id="session-source-llm-plan",
        input_payload={
            "goal": "你是否了解了我们提供给你的素材",
            "interaction_mode": "writing",
        },
    )
    out = planning_node(state)
    mock_planning_llm.assert_not_called()
    assert (out.get("input_payload") or {}).get("thin_execution_profile") == "session_source_qa"
    assert out.get("skip_retrieval") is False


@patch("app.services.intent_observation._invoke_observation_model")
@patch("app.services.llm_client.invoke_structured")
def test_session_source_inquiry_thin_even_with_stale_writing_intent(
    mock_invoke, mock_obs, isolated_stores
):
    mock_obs.return_value = IntentObservationResult(
        source="llm",
        interaction_goal="session_source_inquiry",
        confidence=0.9,
        intent_kind="qa",
        target_mode="qa_mode",
    )
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")
    state = create_initial_state(
        task_id="session-source-stale-intent",
        input_payload={
            "goal": "你是否了解了我们提供给你的素材",
            "writing_intent": {"enabled": True, "source": "prior_turn"},
            "require_planning_after_steer": True,
            "interaction_mode": "writing",
        },
    )
    out = planning_node(state)
    mock_invoke.assert_not_called()
    assert out.get("skip_retrieval") is False
    assert (out.get("input_payload") or {}).get("thin_execution_profile") == "session_source_qa"


def test_route_forces_retrieval_with_stale_writing_intent():
    state = merge_state(
        create_initial_state(),
        skip_retrieval=True,
        input_payload={
            "goal": "你看过我们的素材了么现在？",
            "writing_intent": {"enabled": False},
            "thin_execution_profile": "session_source_qa",
        },
    )
    assert route_after_incremental_planning(state) == "retrieval"


@patch("app.services.intent_observation._invoke_observation_model")
def test_should_skip_session_source_inquiry_planning(mock_obs, regex_fallback):
    mock_obs.return_value = IntentObservationResult(
        source="llm",
        interaction_goal="session_source_inquiry",
        confidence=0.9,
        intent_kind="qa",
        target_mode="qa_mode",
    )
    state = create_initial_state(
        input_payload={"goal": "你看过我们的素材了么", "interaction_mode": "writing"},
    )
    state = run_pre_planning_pipeline(state)
    assert should_skip_session_source_inquiry_planning(state) is True


@patch("app.services.intent_observation._invoke_observation_model")
@patch("app.services.llm_client.invoke_structured")
def test_planning_session_source_inquiry_thin_skips_planning_llm(
    mock_invoke, mock_obs, isolated_stores, regex_fallback
):
    mock_obs.return_value = IntentObservationResult(
        source="llm",
        interaction_goal="session_source_inquiry",
        confidence=0.9,
        intent_kind="qa",
        target_mode="qa_mode",
    )
    mock_invoke.side_effect = AssertionError("full planning LLM must not run")

    state = create_initial_state(
        task_id="session-source-qa-1",
        input_payload={"goal": "你看过我们的素材了么", "interaction_mode": "writing"},
    )
    out = planning_node(state)
    mock_invoke.assert_not_called()

    assert out.get("skip_retrieval") is False
    assert out.get("status") == TaskStatus.PLANNED.value
    actions = out.get("planned_actions") or []
    assert any(a.get("type") == "retrieve" for a in actions)
    assert any(a.get("type") == "answer" for a in actions)
    audits = [
        e for e in (out.get("audit_log") or []) if e.get("action") == "session_source_inquiry_thin"
    ]
    assert audits


def test_route_after_planning_follows_retrieve_action():
    state = merge_state(
        create_initial_state(),
        plan=["retrieve session source", "answer about loaded material"],
        planned_actions=[
            {"type": "retrieve", "params": {"domains": ["source"]}},
            {"type": "answer", "completes_turn": True},
        ],
        skip_retrieval=False,
        input_payload={"writing_intent": {"enabled": False}},
    )
    assert route_after_incremental_planning(state) == "retrieval"
