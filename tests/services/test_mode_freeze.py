"""Mode freeze: prevent within-turn manuscript↔qa oscillation."""

from app.domain.intent_observation import IntentObservationResult
from app.runtime.state import create_initial_state, merge_state
from app.services.intent_snapshot import freeze_intent_snapshot
from app.services.mode_freeze import should_freeze_mode_resolution
from app.services.mode_resolution import run_mode_resolution_pipeline


def test_should_freeze_when_high_confidence_manuscript_aligned():
    state = create_initial_state(
        task_id="mf-1",
        input_payload={
            "goal": "基于已有素材写一本小说",
            "interaction_mode": "writing",
            "route_audit": {
                "inferred_kind": "manuscript",
                "kind_confidence": 0.6,
                "aligned": True,
            },
            "target_mode": "manuscript_mode",
        },
    )
    obs = IntentObservationResult(
        source="structural",
        intent_kind="writing",
        target_mode="manuscript_mode",
        interaction_goal="delivery",
        confidence=0.6,
    )
    state = freeze_intent_snapshot(state, obs, planning_required=True)
    state = merge_state(state, intent_observation=obs.to_dict())
    assert should_freeze_mode_resolution(state) is True


def test_mode_resolution_preserves_manuscript_when_frozen():
    state = create_initial_state(
        task_id="mf-2",
        input_payload={
            "goal": "基于已有素材写一本小说",
            "interaction_mode": "writing",
            "route_audit": {
                "inferred_kind": "manuscript",
                "kind_confidence": 0.6,
                "aligned": True,
            },
            "target_mode": "manuscript_mode",
            "writing_intent": {"enabled": True},
        },
    )
    obs = IntentObservationResult(
        source="structural",
        intent_kind="writing",
        target_mode="manuscript_mode",
        interaction_goal="delivery",
        confidence=0.6,
    )
    state = freeze_intent_snapshot(state, obs, planning_required=True)
    state = merge_state(state, intent_observation=obs.to_dict())

    # Simulate post-planning route audit trying to flip to qa_mode.
    payload = dict(state.get("input_payload") or {})
    payload["intent_kind"] = "qa"
    state = merge_state(state, input_payload=payload)

    updated = run_mode_resolution_pipeline(state)
    assert (updated.get("input_payload") or {}).get("target_mode") == "manuscript_mode"
