"""WRITTEN + steer correction must queue supersede (MISSION_PAUSED), not fail dispatch."""

import pytest

from app.runtime.state import TaskStatus, merge_state
from app.services.graph_runner import GraphRunner
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import queue_steer_message, steer_requires_planning
from app.services.mission_supersede import foreground_operation, supersede_replan_queued
from app.services.state_store import get_state_store


STEER = "基于历史人物的名字来写这份剧本，剧情不要俗套，也不要过于平淡"


def test_queue_steer_from_written_queues_supersede(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.WRITTEN.value,
        current_node="reasoning_or_writing",
        input_payload={"goal": "写一篇悬疑小说", "mission": mission},
    )
    get_state_store().save(state)

    updated = queue_steer_message(
        state["task_id"],
        STEER,
        preempt=True,
        replace_goal=True,
    )
    assert updated.get("status") == TaskStatus.MISSION_PAUSED.value
    payload = updated.get("input_payload") or {}
    assert payload.get("latest_steer_message") == STEER
    assert steer_requires_planning(payload)
    assert supersede_replan_queued(updated)
    op = foreground_operation(updated.get("interrupt_context") or {})
    assert op.get("kind") == "supersede_with_input"
    assert op.get("status") == "replan_queued"


def test_prepare_supersede_allowed_after_written_steer(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.WRITTEN.value,
        input_payload={"goal": "写一篇悬疑小说", "mission": mission},
    )
    get_state_store().save(state)
    queue_steer_message(state["task_id"], STEER, preempt=True, replace_goal=True)

    runner = GraphRunner()
    prepared = runner.prepare_supersede_replan(state["task_id"])
    assert prepared.get("status") == TaskStatus.MISSION_RUNNING.value
    assert (prepared.get("input_payload") or {}).get("foreground_replan_dispatch") is True
