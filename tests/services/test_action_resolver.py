from app.domain.packs.writing import WRITING_PACK
from app.runtime.state import merge_state
from app.services.action_resolver import build_task_snapshot, select_action
from app.services.mission_schema import build_mission_dict, resolve_writing_intent_for_step


def test_select_action_outline_when_missing(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "step_policy": {"first_step": "outline"}}},
        kind="writing",
    )
    state = merge_state(base_state, mission=mission, manuscript={"body_bytes": 0, "outline_bytes": 0})
    snapshot = build_task_snapshot(state, mission)
    selected = select_action(state, mission, WRITING_PACK)
    assert selected.action == "write_outline"
    assert snapshot.has_bootstrap_artifact is False


def test_resolve_writing_intent_delegates_to_resolver(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "total_target_chars": 10000,
                "step_policy": {"chars_per_step": 3000},
            },
        },
        kind="writing",
    )
    state = merge_state(base_state, mission=mission, manuscript={"body_bytes": 0})
    intent = resolve_writing_intent_for_step(state, mission=mission)
    assert intent["action"] == "write_outline"


def test_select_action_append_when_body_exists(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "step_policy": {"chars_per_step": 3000}}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        manuscript={
            "body_bytes": 5000,
            "body_path": "novel.txt",
            "outline_bytes": 200,
            "outline_path": "outline.md",
            "last_chapter_index": 2,
        },
    )
    selected = select_action(state, mission, WRITING_PACK)
    assert selected.action == "append_body"
