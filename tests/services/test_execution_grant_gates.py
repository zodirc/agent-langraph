"""execution_grant bypasses steer gates and stepwise pause."""

from app.runtime.state import merge_state
from app.services.mission_execution import issue_execution_grant_to_payload
from app.services.mission_schema import build_mission_dict
from app.services.progress_evaluator import evaluate_mission_control


def test_grant_bypasses_outcome_gate_pause(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    mission = {**mission, "orchestration": {"enabled": True, "stepwise": True}}
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=3,
        input_payload={
            **issue_execution_grant_to_payload(
                {"steer_outcome_pending_confirm": True},
                source="continue_signal",
            ),
        },
    )
    result = evaluate_mission_control(state)
    assert result.action == "continue"
    assert not result.done
