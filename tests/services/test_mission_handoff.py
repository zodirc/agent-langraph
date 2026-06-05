"""Mission handoff: single entry, preserve mission_step, loop detection."""

from app.runtime.state import TaskStatus, merge_state
from app.services.graph_runner import GraphRunner
from app.services.mission_handoff import (
    complete_mission_handoff,
    detect_handoff_planning_loop,
    mission_handoff_needed,
    record_planning_handoff_attempt,
)
from app.services.mission_schema import build_mission_dict
from app.services.mission_service import init_mission_state
from app.services.session_turn import _reset_execution_fields


def test_complete_mission_handoff_preserves_mission_step(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        mission_step=3,
        progress={"phase": "executing", "steps_completed": 2},
        input_payload={"goal": "继续", "mission": mission},
    )
    handed = complete_mission_handoff(state, state["input_payload"])
    assert int(handed.get("mission_step") or 0) == 3
    assert handed.get("mission")
    assert (handed.get("input_payload") or {}).get("mission_handoff_completed") is True
    assert handed.get("status") == TaskStatus.MISSION_RUNNING.value


def test_reset_execution_fields_preserves_mission_step_on_continue(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    existing = merge_state(
        base_state,
        mission=mission,
        mission_step=2,
        session_turn=2,
        input_payload={"goal": "写小说"},
    )
    payload = {"goal": "继续", "mission": mission}
    reset = _reset_execution_fields(existing, payload)
    assert int(reset.get("mission_step") or 0) == 2
    assert int(reset.get("session_turn") or 0) == 3


def test_detect_handoff_planning_loop(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    payload = {
        "goal": "继续",
        "turn_contract": {"primary_op": "write_outline"},
        "_planning_enter_count": 2,
        "_last_planning_contract_sig": "t3|r0|write_outline|继续",
    }
    state = merge_state(base_state, mission=mission, session_turn=3, input_payload=payload)
    assert detect_handoff_planning_loop(payload, state) is True


def test_record_planning_handoff_attempt_increments(base_state):
    state = merge_state(base_state, session_turn=3, input_payload={"goal": "继续"})
    out = record_planning_handoff_attempt(state["input_payload"], state)
    assert int(out.get("_planning_enter_count") or 0) == 1
    assert "t3|" in str(out.get("_last_planning_contract_sig") or "")


def test_mission_handoff_needed_for_manuscript_mode(base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    payload = {
        "goal": "继续",
        "target_mode": "manuscript_mode",
        "execution_path": "mission_writing",
        "mission": mission,
    }
    state = merge_state(base_state, input_payload=payload, execution_mode="single")
    assert mission_handoff_needed(state, payload, execution_mode="single") is True


def test_invoke_graph_safe_handoff_after_planning(monkeypatch, base_state):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "total_target_chars": 50000}},
        kind="writing",
    )
    planned = merge_state(
        base_state,
        mission=mission,
        mission_step=1,
        status=TaskStatus.PLANNED.value,
        plan=["contract: write_outline"],
        input_payload={
            "goal": "继续",
            "target_mode": "manuscript_mode",
            "execution_path": "mission_writing",
            "mission": mission,
            "turn_contract": {"primary_op": "write_outline"},
        },
    )
    mission_final = merge_state(planned, status=TaskStatus.MISSION_PAUSED.value)

    def fake_stream_graph(state, *, thread_id):
        yield "planning", planned

    def fake_run_mission_graph(state, *, thread_id):
        return mission_final

    monkeypatch.setattr(
        "app.services.graph_runner.stream_graph",
        fake_stream_graph,
    )
    monkeypatch.setattr(
        "app.services.graph_runner.run_mission_graph",
        fake_run_mission_graph,
    )

    out = GraphRunner()._invoke_graph_safe(planned, thread="t", mode="single")
    assert out.get("status") == TaskStatus.MISSION_PAUSED.value


def test_init_mission_state_still_creates_fresh_mission(base_state):
    payload = {
        "goal": "写小说",
        "mission": {"kind": "writing", "total_target_chars": 10000},
    }
    state = init_mission_state(base_state, payload)
    assert state.get("mission")
    assert int(state.get("mission_step") or 0) == 0
