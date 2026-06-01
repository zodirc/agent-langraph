"""Mission execution grant, reconcile, and checkpoint summary."""

from app.runtime.state import merge_state
from app.services.mission_execution import (
    compute_artifact_delta,
    consume_execution_grant,
    has_execution_grant,
    issue_execution_grant,
    issue_execution_grant_to_payload,
    reconcile_work_plan,
    work_item_satisfied,
)
from app.services.mission_orchestrator import get_current_work_item, orchestration_enabled
from app.services.mission_schema import build_mission_dict
from app.services.progress_evaluator import evaluate_mission_control
from app.services.session.turn_policy import TurnDecision


def test_issue_and_consume_execution_grant(base_state):
    state = issue_execution_grant(base_state, source="resume_api")
    assert has_execution_grant(state=state)
    state = consume_execution_grant(state)
    assert not has_execution_grant(state=state)
    assert (state.get("input_payload") or {}).get("last_execution_grant")


def test_work_item_satisfied_outline(base_state):
    mission = {"step_policy": {"first_step": "outline"}}
    state = merge_state(
        base_state,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 500},
    )
    assert work_item_satisfied("write_outline", state=state, mission=mission) is True


def test_reconcile_marks_duplicate_outline_done(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "total_target_chars": 50000,
                "step_policy": {"first_step": "outline", "chars_per_step": 4000},
            },
        },
        kind="writing",
    )
    mission = {**mission, "orchestration": {"enabled": True, "stepwise": True}}
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 27863, "body_bytes": 0},
        progress={
            "work_plan": {
                "mode": "lazy",
                "items": [
                    {"id": "wi-1", "kind": "write_outline", "title": "write_outline", "status": "done"},
                    {"id": "wi-2", "kind": "write_outline", "title": "write_outline", "status": "pending"},
                ],
                "completed_ids": ["wi-1"],
            }
        },
    )
    state = reconcile_work_plan(state)
    items = (state.get("progress") or {}).get("work_plan", {}).get("items") or []
    pending_outline = [i for i in items if i.get("kind") == "write_outline" and i.get("status") == "pending"]
    assert not pending_outline
    current = get_current_work_item(state)
    assert current is None or current.get("kind") != "write_outline"


def test_execution_grant_overrides_stepwise_pause(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    mission = {**mission, "orchestration": {"enabled": True, "stepwise": True}}
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=2,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1000, "body_bytes": 0},
        input_payload=issue_execution_grant_to_payload({}, source="resume_api"),
    )
    result = evaluate_mission_control(state)
    assert result.action == "continue"
    assert not result.done


def test_issue_execution_grant_clears_forced_pause_intervention():
    payload = issue_execution_grant_to_payload(
        {
            "mission_intervention": {"action": "pause", "force": True, "reason": "stop now"},
        },
        source="resume_api",
    )
    assert payload.get("execution_grant")
    assert payload.get("mission_intervention") is None


def test_mechanical_resume_decision_sources():
    assert TurnDecision(intent="resume_mission", source="continue_signal").source == "continue_signal"
    from app.services.mission_execution import is_mechanical_resume_decision

    assert is_mechanical_resume_decision(
        TurnDecision(intent="resume_mission", source="continue_signal", reason="x")
    )
    assert not is_mechanical_resume_decision(
        TurnDecision(intent="isolate_qa", source="continue_signal", reason="x")
    )


def test_artifact_delta_detects_body_growth():
    before = {"body_path": "novel.txt", "body_bytes": 100}
    after = {"body_path": "novel.txt", "body_bytes": 4200}
    delta = compute_artifact_delta(before, after)
    assert delta["has_change"] is True
    assert delta["body"]["delta_bytes"] == 4100
