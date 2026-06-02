from app.domain.packs.writing import WRITING_PACK
from app.runtime.state import merge_state
from app.services.mission_orchestrator import apply_work_plan_to_payload


def _base_mission_state(base_state, *, work_item: dict):
    mission = {
        "kind": "writing",
        "success_criteria": {"type": "metric_gte", "metric": "written_chars", "target": 12000},
        "step_policy": {"chars_per_step": 1200},
        "orchestration": {"enabled": True, "stepwise": True},
    }
    progress = {
        "work_plan": {
            "version": 1,
            "mode": "lazy",
            "items": [work_item],
            "current_id": None,
            "completed_ids": [],
            "total_items": 1,
        }
    }
    return merge_state(base_state, mission=mission, progress=progress)


def test_writing_pack_tools_include_session_file_tools():
    for tool in ("ls_path", "read_file", "grep_file", "replace_in_file"):
        assert tool in WRITING_PACK.tools


def test_apply_work_plan_injects_patch_recent_chapter_tools(base_state):
    state = _base_mission_state(
        base_state,
        work_item={
            "id": "wi-1",
            "kind": "patch_recent_chapter",
            "title": "patch",
            "status": "pending",
            "params": {
                "filename": "novel.txt",
                "old_text": "foo",
                "new_text": "bar",
                "replace_all": False,
                "auto_tools": True,
            },
        },
    )
    out = apply_work_plan_to_payload(state)
    payload = out["input_payload"]
    assert payload["current_work_item"]["kind"] == "patch_recent_chapter"
    assert payload["selected_tools"] == ["read_file", "grep_file", "replace_in_file"]
    assert payload["tool_params"]["read_file"]["path"] == "novel.txt"
    assert payload["tool_params"]["replace_in_file"]["dry_run"] is True


def test_apply_work_plan_injects_consistency_tools(base_state):
    state = _base_mission_state(
        base_state,
        work_item={
            "id": "wi-2",
            "kind": "consistency_check",
            "title": "consistency",
            "status": "pending",
            "params": {"filename": "novel.txt", "auto_tools": True},
        },
    )
    out = apply_work_plan_to_payload(state)
    payload = out["input_payload"]
    assert payload["selected_tools"] == ["ls_path", "read_file", "grep_file"]
    assert payload["tool_params"]["grep_file"]["regex"] is True


def test_apply_work_plan_default_does_not_auto_inject(base_state):
    state = _base_mission_state(
        base_state,
        work_item={
            "id": "wi-3",
            "kind": "patch_recent_chapter",
            "title": "patch",
            "status": "pending",
            "params": {"filename": "novel.txt"},
        },
    )
    out = apply_work_plan_to_payload(state)
    payload = out["input_payload"]
    assert "selected_tools" not in payload or payload.get("selected_tools") in ([], None)

