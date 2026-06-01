"""Tests for bounded SRDL runner (rule-based deliberation path)."""

from __future__ import annotations

from app.config.settings import Settings
from app.domain.react_loop import get_react_loop
from app.runtime.state import create_initial_state, merge_state
from app.services.react_audit import react_metrics_from_state
from app.services.react_entry import init_react_loop_state
from app.services.react_loop_runner import (
    deliberate_next_action,
    execute_bounded_action,
    finalize_loop,
    record_react_observation,
    rule_based_decision,
    should_finish_loop,
)


def _running_loop_state(**payload):
    state = create_initial_state(
        task_id="react-run-1",
        input_payload={"goal": "Explain runtime", "risk_level": "LOW", **payload},
    )
    loop_dict = init_react_loop_state(state)
    return merge_state(state, react_loop=loop_dict, plan=["retrieve", "reason"])


def test_rule_based_prefers_knowledge_retrieval(test_settings: Settings):
    loop = get_react_loop(_running_loop_state())
    decision = rule_based_decision(_running_loop_state(), loop)
    assert decision.action == "retrieve_knowledge"
    assert decision.continue_loop is True


def test_deliberate_records_audit_events(test_settings: Settings, isolated_stores):
    state, decision = deliberate_next_action(_running_loop_state())
    assert decision.action in get_react_loop(state).allowed_actions
    events = (state.get("turn_event_log") or {}).get("events") or []
    types = [e.get("event_type") for e in events]
    assert "react_step_started" in types
    assert "react_action_selected" in types


def test_execute_retrieve_and_observe(test_settings: Settings, isolated_stores):
    state, decision = deliberate_next_action(_running_loop_state())
    updated, obs = execute_bounded_action(state, decision)
    assert obs.get("status") == "ok"
    assert obs.get("action") == "retrieve_knowledge"

    updated = record_react_observation(updated, decision, obs)
    loop = get_react_loop(updated)
    assert len(loop.history) == 1
    assert loop.step_index == 1
    assert updated.get("turn_facts") is not None


def test_finish_loop_sets_exit_fields(test_settings: Settings):
    state = finalize_loop(_running_loop_state(), reason="enough_information", exit_path="finish_with_answer")
    loop = get_react_loop(state)
    assert loop.status == "finished"
    assert loop.exit_reason == "enough_information"
    assert loop.enabled is False
    metrics = react_metrics_from_state(state)
    assert metrics["loop_status"] == "finished"


def test_should_finish_on_finish_action(test_settings: Settings):
    from app.domain.react_loop import ReactDecision

    loop = get_react_loop(_running_loop_state())
    decision = ReactDecision(
        thought_summary="done",
        action="finish",
        action_input={},
        continue_loop=False,
        why="ok",
        confidence=0.9,
    )
    assert should_finish_loop(_running_loop_state(), loop, decision, {"action": "finish", "status": "ok"})
