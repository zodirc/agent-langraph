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
    command = payload.get("writing_command") or {}
    assert command.get("action") == "review_outline"
    assert command.get("target_filename") == "outline.txt"


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
    command = payload.get("writing_command") or {}
    assert command.get("action") == "review_outline"
    assert command.get("target_filename") == "outline.txt"


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
    from app.services.graph_run_registry import begin_graph_run

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(base_state, mission=mission, status="MISSION_RUNNING")
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(state, execution_run={"run_id": run_id})
    get_state_store().save(state)

    queue_steer_message(state["task_id"], "第一条 steer", preempt=False)
    queue_steer_message(state["task_id"], "第二条 steer", preempt=False)
    loaded = get_state_store().load(state["task_id"])
    entries = normalize_pending_entries(loaded.get("pending_user_message"))
    assert len(entries) == 2


def test_queue_steer_after_completed_starts_new_turn(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status="COMPLETED",
        input_payload={"goal": "写小说"},
        session_turn=1,
    )
    get_state_store().save(state)

    updated = queue_steer_message(
        state["task_id"],
        "写一个2048的小游戏",
        replace_goal=True,
    )
    assert updated["status"] == "NEW"
    assert int(updated.get("session_turn") or 0) == 2
    payload = updated.get("input_payload") or {}
    assert "2048" in str(payload.get("goal") or "")


def test_queue_steer_while_running_stages_goal_immediately(base_state, monkeypatch):
    from app.services.graph_run_registry import begin_graph_run

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    steer_text = "基于原电影编写，人物需要为原电影人物，只改动剧情走向"
    state = merge_state(
        base_state,
        mission=mission,
        status="MISSION_RUNNING",
        input_payload={
            "goal": "写一部长篇小说",
            "turn_contract": {"primary_op": "reasoning"},
        },
    )
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(state, execution_run={"run_id": run_id})
    get_state_store().save(state)

    updated = queue_steer_message(
        state["task_id"],
        steer_text,
        preempt=True,
    )
    payload = updated.get("input_payload") or {}
    goal = str(payload.get("goal") or "")
    assert "原电影" in goal
    assert not updated.get("pending_user_message")
    assert updated.get("status") == "MISSION_PAUSED"
    from app.runtime.state_field_access import mission_control_from_state

    assert mission_control_from_state(updated).get("pause_reason") == "superseded_by_new_input"
    assert payload.get("require_planning_after_steer") is True
    assert payload.get("foreground_preempt_consumed") is True
    assert not (payload.get("turn_contract") or {}).get("primary_op")


def test_preempt_steer_enters_replan_without_waiting_for_step_boundary(base_state, monkeypatch):
    from app.services.graph_run_registry import begin_graph_run

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    steer_text = "我认为你需要使用原电影的人物，只是在一些原电影的剧情走向上改动"
    state = merge_state(
        base_state,
        mission=mission,
        status="MISSION_RUNNING",
        input_payload={
            "goal": "写同人小说",
            "turn_contract": {"primary_op": "reasoning"},
        },
    )
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(state, execution_run={"run_id": run_id})
    get_state_store().save(state)

    updated = queue_steer_message(state["task_id"], steer_text, preempt=True)
    ctx = updated.get("interrupt_context") or {}
    assert str(ctx.get("control_state") or "") == "REPLANNING"
    from app.services.client_display import build_steer_task_client_display

    display = build_steer_task_client_display(updated, queued=False)
    text = "\n".join(display["system_lines"])
    assert "steer_applied" in text
    assert "steer_replan_pending" in text
    assert display.get("supersede_stream_recommended") is True
    assert "steer_replan_pending" in text
    plan = (updated.get("progress") or {}).get("work_plan") or {}
    pending_items = [
        i for i in (plan.get("items") or []) if str(i.get("status") or "") == "pending"
    ]
    assert not pending_items


def test_forced_pause_queue_also_sets_immediate_intervention(base_state):
    from app.services.graph_run_registry import begin_graph_run

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(base_state, mission=mission, status="MISSION_RUNNING", input_payload={})
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(state, execution_run={"run_id": run_id})
    get_state_store().save(state)

    queue_steer_message(
        state["task_id"],
        "",
        intervention={"action": "pause", "force": True, "reason": "stop now"},
        priority=100,
        preempt=True,
    )
    loaded = get_state_store().load(state["task_id"])
    payload = loaded.get("input_payload") or {}
    intervention = payload.get("mission_intervention") or {}
    assert intervention.get("action") == "pause"
    assert intervention.get("force") is True
    assert loaded.get("status") == "MISSION_PAUSED"
    from app.runtime.state_field_access import mission_control_from_state

    control = mission_control_from_state(loaded)
    assert control.get("action") == "pause"
    pending = normalize_pending_entries(loaded.get("pending_user_message"))
    assert len(pending) == 1
