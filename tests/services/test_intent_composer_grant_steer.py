"""Intent composer — grant vs steer planning precedence."""

from app.runtime.state import merge_state
from app.services.intent_composer import (
    grant_may_mechanical_forward,
    may_issue_execution_grant,
    record_grant_steer_conflict,
)
from app.services.mission_execution import issue_execution_grant_to_payload
from app.services.mission_steer import apply_steer_planning_gate
from app.services.mission_schema import build_mission_dict
from app.services.turn_contract import planning_fallback_from_state


def test_grant_blocked_when_steer_planning_pending(base_state):
    payload = apply_steer_planning_gate({"goal": "review all chapters"})
    assert not may_issue_execution_grant(payload)
    assert not grant_may_mechanical_forward(payload)


def test_issue_grant_refused_when_steer_pending(base_state):
    payload = apply_steer_planning_gate({})
    out = issue_execution_grant_to_payload(payload, source="resume_api")
    assert "execution_grant" not in out
    assert out.get("grant_steer_conflict")


def test_grant_forward_fallback_blocked_under_steer(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={
            "body_bytes": 5000,
            "last_chapter_index": 3,
            "outline_bytes": 1000,
        },
        input_payload=issue_execution_grant_to_payload(
            apply_steer_planning_gate({"steer_applied_at": "t", "goal": "batch review"}),
            source="resume_api",
        ),
        progress={
            "work_plan": {
                "items": [
                    {
                        "id": "wi-1",
                        "kind": "append_body",
                        "status": "pending",
                        "title": "append",
                    }
                ]
            }
        },
    )
    fb = planning_fallback_from_state(state)
    assert fb is not None
    assert fb.get("fallback_reason") == "batch_unit_work_plan"


def test_grant_forward_fallback_when_grant_allowed(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"outline_path": "outline.txt", "outline_bytes": 1000, "body_bytes": 500},
        input_payload=issue_execution_grant_to_payload(
            {"steer_planning_done": True},
            source="continue_signal",
        ),
    )
    fb = planning_fallback_from_state(state)
    assert fb is not None
    assert fb.get("fallback_reason") == "execution_grant_forward"


def test_record_grant_steer_conflict_strips_grant():
    payload = issue_execution_grant_to_payload(
        {"steer_planning_done": True},
        source="continue_signal",
    )
    assert payload.get("execution_grant")
    cleared = record_grant_steer_conflict(payload, reason="test")
    assert "execution_grant" not in cleared
    assert cleared.get("grant_steer_conflict")
