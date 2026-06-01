from app.domain.decision import apply_verdict_to_reflection, build_reflection_verdict
from app.runtime.state import create_initial_state, merge_state
from app.services.turn_event_log import record_turn_event


def test_build_verdict_replan_on_plan_rejected():
    state = create_initial_state(task_id="t1")
    state = record_turn_event(
        state,
        "plan_rejected",
        "planning",
        "planning",
        {"issues": ["plan_too_vague"]},
    )
    reflection = {"issues": ["plan_rejected: plan_too_vague"], "critique": "plan vague"}
    verdict = build_reflection_verdict(state, reflection)
    assert verdict.failure_type == "plan_vague"
    assert verdict.recommended_action == "replan"


def test_apply_verdict_sets_retry_flags():
    from app.domain.decision import ReflectionVerdict

    verdict = ReflectionVerdict(
        failure_type="reasoning_quality",
        root_cause="low confidence",
        recoverable=True,
        recommended_action="retry_same_step",
        confidence=0.6,
    )
    reflection = apply_verdict_to_reflection({"critique": "x"}, verdict)
    assert reflection["retry_reasoning"] is True
    assert reflection["retry_planning"] is False
    assert reflection["verdict"]["recommended_action"] == "retry_same_step"


def test_build_verdict_proceed_when_clean():
    state = create_initial_state(task_id="t1")
    reflection = {"issues": [], "critique": "no issues detected"}
    verdict = build_reflection_verdict(state, reflection)
    assert verdict.recommended_action == "proceed"
