"""Steer queue merge, goal append, review_outline intervention (no keyword regex)."""

from app.runtime.state import merge_state
from app.services.mission_intervention import apply_intervention_to_payload
from app.services.mission_orchestrator import orchestration_detail, orchestration_summary
from app.services.mission_routing import patch_mission_from_planning
from app.services.mission_steer import (
    apply_review_outline_mode,
    apply_steer_message,
    build_pending_queue,
    consume_pending_steer,
    normalize_pending_entries,
    queue_steer_message,
    review_outline_requested,
)
from app.services.mission_schema import build_mission_dict
from app.services.state_store import get_state_store


def test_review_outline_via_explicit_intervention(base_state):
    mission = {"kind": "writing", "step_policy": {"outline_artifact": "outline.txt"}}
    payload = apply_intervention_to_payload(
        {"goal": "检阅大纲"},
        {
            "action": "review_outline",
            "force": False,
        },
    )
    assert review_outline_requested(payload) is True
    assert (payload.get("writing_intent") or {}).get("enabled") is False
    assert payload.get("tool_params", {}).get("read_text_artifact", {}).get("filename") == "outline.txt"


def test_apply_steer_message_does_not_regex_infer_review(base_state):
    """Natural-language steer only sets planning gate; review_outline comes from planning LLM."""
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 10000}},
        kind="writing",
    )
    state = merge_state(base_state, mission=mission)
    out = apply_steer_message(state, "我需要检阅你重写后的大纲")
    payload = out.get("input_payload") or {}
    assert payload.get("require_planning_after_steer") is True
    assert not payload.get("steer_review_outline")


def test_apply_review_outline_mode_disables_writing(base_state):
    mission = {"kind": "writing", "step_policy": {"outline_artifact": "outline.txt"}}
    payload = apply_review_outline_mode({"goal": "x"}, mission)
    assert payload.get("steer_review_outline") is True
    assert (payload.get("writing_intent") or {}).get("enabled") is False
    assert "read_text_artifact" in (payload.get("selected_tools") or [])


def test_build_pending_queue_merges_messages(base_state):
    first = build_pending_queue(None, message="按《岁月》人物重写大纲")
    second = build_pending_queue(first, message="全书120万字，每章4000字，共300章")
    entries = normalize_pending_entries(second)
    assert len(entries) == 2
    assert "岁月" in second["message"]
    assert "120万" in second["message"]


def test_consume_pending_applies_all_queued_messages(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 10000}},
        kind="writing",
    )
    pending = build_pending_queue(
        None,
        message="按《岁月》人物重写大纲",
    )
    pending = build_pending_queue(
        pending,
        message="全书120万字，每章4000字",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status="MISSION_RUNNING",
        pending_user_message=pending,
    )
    out = consume_pending_steer(state)
    goal = (out.get("input_payload") or {}).get("goal") or ""
    history = out.get("conversation_history") or []
    assert "岁月" in goal
    assert "120万" in goal
    assert len(history) >= 2
    assert not out.get("pending_user_message")


def test_apply_steer_appends_goal_not_replace(base_state):
    state = merge_state(
        base_state,
        input_payload={"goal": "写一部长篇历史小说"},
    )
    out = apply_steer_message(state, "加入《岁月》电视剧人物")
    goal = (out.get("input_payload") or {}).get("goal") or ""
    assert "历史小说" in goal
    assert "岁月" in goal


def test_patch_mission_updates_total_target_chars(base_state):
    mission = build_mission_dict(
        base_state,
        {
            "mission": {
                "kind": "writing",
                "total_target_chars": 50000,
                "step_policy": {"chars_per_step": 3000},
            },
        },
        kind="writing",
    )
    payload = {"mission": mission, "goal": "续写"}
    patched, reason = patch_mission_from_planning(
        {"total_target_chars": 1_200_000, "step_policy": {"chars_per_step": 4000}},
        payload,
        state_mission=mission,
    )
    assert reason == "planning_mission_patch"
    m = patched["mission"]
    assert m["total_target_chars"] == 1_200_000
    assert m["step_policy"]["chars_per_step"] == 4000
    assert m["success_criteria"]["target"] == 1_200_000.0


def test_orchestration_summary_distinguishes_done_and_current(base_state):
    mission = {"kind": "writing", "orchestration": {"enabled": True}}
    state = merge_state(
        base_state,
        mission=mission,
        progress={
            "work_plan": {
                "mode": "lazy",
                "items": [
                    {"id": "a", "kind": "write_outline", "title": "write_outline", "status": "done"},
                    {"id": "b", "kind": "write_outline", "title": "write_outline", "status": "pending"},
                ],
                "current_id": "b",
                "completed_ids": ["a"],
            }
        },
    )
    detail = orchestration_detail(state)
    assert detail["done"] == 1
    assert detail["total"] == 2
    assert "write_outline" in detail["completed"]
    assert detail["current_status"] == "pending"
    summary = orchestration_summary(state)
    assert "已完成 1/2" in summary
    assert "当前：" in summary


def test_queue_steer_while_running_accumulates(base_state, monkeypatch):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(base_state, mission=mission, status="MISSION_RUNNING")
    get_state_store().save(state)

    queue_steer_message(state["task_id"], "第一条 steer")
    queue_steer_message(state["task_id"], "第二条 steer")
    loaded = get_state_store().load(state["task_id"])
    entries = normalize_pending_entries(loaded.get("pending_user_message"))
    assert len(entries) == 2
