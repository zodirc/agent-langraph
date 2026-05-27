from app.services.mission_intervention import (
    apply_intervention_to_payload,
    intervention_from_payload,
    is_forced,
)
from app.services.mission_schema import apply_mission_step_to_payload, resolve_writing_intent_for_step
from app.runtime.state import merge_state
from app.services.mission_schema import build_mission_dict


def test_apply_planning_intervention_from_llm():
    from app.services.mission_intervention import apply_planning_intervention

    payload = apply_planning_intervention(
        {
            "mission_intervention": {
                "action": "edit_plot",
                "force": True,
                "edit_spec": {"filename": "novel.txt", "old_text": "a", "new_text": "b"},
            }
        },
        {"goal": "第2章不对，请改对话"},
    )
    assert payload.get("mission_intervention", {}).get("force") is True
    assert payload.get("revision_intent") == "edit_plot"


def test_intervention_from_payload_forced():
    payload = {
        "mission_intervention": {
            "action": "rewrite_outline",
            "force": True,
        }
    }
    block = intervention_from_payload(payload)
    assert block is not None
    assert block["action"] == "rewrite_outline"
    assert is_forced(block)


def test_detect_revision_intent_removed():
    from app.services.manuscript_intent import detect_revision_intent

    assert detect_revision_intent("不认可大纲") is None


def test_apply_mission_step_forced_intervention(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 10000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "mission_intervention": {
                "action": "reset_body",
                "force": True,
            }
        },
    )
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent["action"] == "reset_body"
    assert intent.get("forced") is True


def test_apply_mission_step_skips_planning_on_message_steer(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "total_target_chars": 10000,
                "orchestration": {"enabled": False},
            }
        },
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={
            "steer_applied_at": "2026-01-01T00:00:00Z",
            "require_planning_after_steer": True,
            "goal": "改一下第2章",
        },
    )
    payload = apply_mission_step_to_payload(state)
    assert payload.get("skip_planning_llm") is False


def test_resolve_writing_intent_rewrite_outline(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "step_policy": {"first_step": "outline", "chars_per_step": 3000},
            },
        },
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={"body_bytes": 5000, "outline_bytes": 2000, "body_path": "novel.txt"},
        input_payload={
            "mission_intervention": {"action": "rewrite_outline", "force": True},
        },
    )
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent["action"] == "write_outline"
