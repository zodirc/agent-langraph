from app.runtime.state import create_initial_state, merge_state
from app.services.fact_layer import build_turn_facts
from app.services.turn_event_log import get_turn_event_log, record_turn_event


def test_record_and_build_turn_facts_with_events():
    state = create_initial_state(task_id="t1", input_payload={"goal": "test"})
    state = record_turn_event(
        state,
        "plan_generated",
        "planning",
        "planning",
        {"steps": 2},
    )
    state = record_turn_event(
        state,
        "tool_invoked",
        "echo",
        "tool_execution",
        {"status": "ok"},
    )
    log = get_turn_event_log(state)
    assert len(log.events) == 2
    facts = build_turn_facts(state)
    assert facts["event_count"] == 2
    assert len(facts["events"]) == 2
    assert len(facts["execution_facts"]) == 1
    assert len(facts["decision_facts"]) == 1


def test_failure_events_detect_plan_rejected():
    state = create_initial_state(task_id="t1")
    state = record_turn_event(
        state,
        "plan_rejected",
        "planning",
        "planning",
        {"issues": ["plan_too_vague"]},
    )
    failures = get_turn_event_log(state).failure_events()
    assert len(failures) == 1
    assert failures[0].event_type == "plan_rejected"
