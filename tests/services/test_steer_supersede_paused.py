"""PAUSED steer must queue supersede with latest_steer_message."""

from app.runtime.state import TaskStatus, merge_state
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import apply_steer_message, queue_steer_message
from app.services.mission_supersede import foreground_operation
from app.services.state_store import get_state_store

STEER = "我认为你需要使用原电影的人物，只是在一些原电影的剧情走向上改动，也不需要架空人物"


def _mission_control(state: dict) -> dict:
    mc = state.get("mission_control")
    if isinstance(mc, dict):
        return mc
    plan_graph = state.get("plan_graph") or {}
    meta = plan_graph.get("meta") if isinstance(plan_graph, dict) else {}
    if isinstance(meta, dict) and isinstance(meta.get("mission_control"), dict):
        return meta["mission_control"]
    return {}


def test_paused_steer_queues_supersede_with_latest_message(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload={"goal": "写一部长篇小说"},
    )
    get_state_store().save(state)

    updated = apply_steer_message(state, STEER, source="api_steer", persist=True)
    payload = updated.get("input_payload") or {}
    assert payload.get("latest_steer_message") == STEER
    assert payload.get("require_planning_after_steer") is True
    assert _mission_control(updated).get("pause_reason") == "superseded_by_new_input"
    op = foreground_operation(updated.get("interrupt_context") or {})
    assert op.get("kind") == "supersede_with_input"
    assert op.get("status") == "replan_queued"
    assert int(payload.get("intent_revision") or 0) >= 1


def test_repeat_paused_steer_bumps_revision(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload={"goal": "写同人"},
    )
    get_state_store().save(state)

    first = apply_steer_message(state, STEER, source="api_steer", persist=True)
    rev1 = int((first.get("input_payload") or {}).get("intent_revision") or 0)
    second = apply_steer_message(first, STEER, source="api_steer", persist=True)
    rev2 = int((second.get("input_payload") or {}).get("intent_revision") or 0)
    assert rev2 > rev1


def test_running_preempt_sets_latest_steer_message(base_state):
    from app.services.graph_run_registry import begin_graph_run

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_RUNNING.value,
        input_payload={"goal": "写同人"},
    )
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(state, execution_run={"run_id": run_id})
    get_state_store().save(state)

    updated = queue_steer_message(state["task_id"], STEER, preempt=True)
    payload = updated.get("input_payload") or {}
    assert payload.get("latest_steer_message") == STEER
