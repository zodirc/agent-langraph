"""Steer/stream single SSE entry."""

from app.runtime.state import TaskStatus, merge_state
from app.services.graph_runner import GraphRunner
from app.services.mission_handoff import _handoff_signature
from app.services.mission_steer import queue_steer_message
from app.services.state_store import get_state_store

STEER = "我认为你需要使用原电影的人物，只是在一些原电影的剧情走向上改动，也不需要架空人物"


def test_handoff_signature_includes_steer_revision(base_state):
    state = merge_state(base_state, session_turn=3)
    sig_a = _handoff_signature(
        {"goal": "写同人", "turn_contract": {"primary_op": "write_outline"}, "intent_revision": 1},
        state,
    )
    sig_b = _handoff_signature(
        {
            "goal": "写同人",
            "latest_steer_message": "使用原电影人物",
            "turn_contract": {"primary_op": "write_outline"},
            "intent_revision": 2,
        },
        state,
    )
    assert sig_a != sig_b
    assert "steer:" in sig_b


def test_stream_steer_mission_yields_graph_events(base_state, monkeypatch):
    runner = GraphRunner()
    events: list[str] = []

    def fake_stream_supersede(task_id, *, quiet=False):
        yield 'event: progress\ndata: {"task_id":"t","message":"planning"}\n\n'

    monkeypatch.setattr(runner, "steer_mission", lambda *a, **k: merge_state(base_state, status="MISSION_PAUSED"))
    monkeypatch.setattr(runner, "stream_supersede_mission", fake_stream_supersede)

    from app.services.state_store import get_state_store
    from app.services.mission_steer import apply_steer_planning_gate

    state = merge_state(
        base_state,
        status="MISSION_PAUSED",
        input_payload=apply_steer_planning_gate(
            {"goal": "写同人", "latest_steer_message": "原电影人物"}
        ),
    )
    get_state_store().save(state)

    for chunk in runner.stream_steer_mission(state["task_id"], "原电影人物", preempt=True):
        events.append(chunk)

    assert any("progress" in e for e in events)


def test_stream_steer_syncs_latest_message_when_goal_stale(base_state):
    runner = GraphRunner()
    mission = {"kind": "writing", "objective": "写暗战同人"}
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload={
            "goal": "请写一篇关于 暗战 电影的小说，允许改动原剧情",
            "require_planning_after_steer": True,
            "steer_planning_done": False,
            "mission": mission,
        },
    )
    get_state_store().save(state)

    synced = runner._sync_steer_message_for_stream(
        state["task_id"],
        STEER,
        replace_goal=True,
    )
    payload = synced.get("input_payload") or {}
    assert payload.get("latest_steer_message") == STEER
    assert payload.get("goal") == STEER


def test_planned_status_steer_applies_immediately(base_state):
    mission = {"kind": "writing"}
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.PLANNED.value,
        input_payload={"goal": "请写一篇关于 暗战 电影的小说，允许改动原剧情", "mission": mission},
    )
    get_state_store().save(state)

    updated = queue_steer_message(state["task_id"], STEER, replace_goal=True)
    payload = updated.get("input_payload") or {}
    assert payload.get("latest_steer_message") == STEER
    assert payload.get("goal") == STEER
    assert payload.get("require_planning_after_steer") is True
