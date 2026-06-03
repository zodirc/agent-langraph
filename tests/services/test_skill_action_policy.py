from app.services.action_resolver import CandidateAction
from app.services.skill_action_policy import apply_skill_action_weights


def test_apply_skill_action_weights_boosts_action():
    scored = [
        CandidateAction(action="append_body", preconditions_met=True, score=0.6, reason="a"),
        CandidateAction(action="write_outline", preconditions_met=True, score=0.7, reason="b"),
    ]
    state = {
        "skill_runtime_policy": {
            "resolved_action_weights": {"append_body": 0.3},
        },
        "skill_snapshot": {"action_policy": {"fallback_action": "append_body"}},
    }
    out = apply_skill_action_weights(scored, state=state)
    assert out[0].action == "append_body"
    assert out[0].score > 0.6
