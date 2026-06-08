"""Control plane regression canon (optimization.md §9).

R1–R11 gate control-plane PRs. Tests focus on routing invariants without full LLM.
"""

from __future__ import annotations

import pytest

from app.runtime.state import TaskStatus, merge_state
from app.services.event_classification import classify_user_event
from app.services.graph_runner import GraphRunner
from app.services.invariant_guard import InvariantViolation, validate, validate_fsm_transition
from app.services.mission_schema import build_mission_dict
from app.services.mission_steer import apply_steer_planning_gate
from app.services.mission_supersede import mark_supersede_replan_queued
from app.services.run_controller import RunCancelled, RunController
from app.services.session_controller import SessionController
from app.services.session_fsm import FSM_REPLANNING, derive_fsm_from_legacy, get_fsm_state, set_fsm_state
from app.services.state_store import get_state_store
from app.services.turn_guard import can_finalize_turn, contract_requires_execution_route


def _writing_mission_state(base_state, **overrides):
    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing", "orchestration": {"enabled": True}}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.MISSION_PAUSED.value,
        input_payload={"goal": "写同人", "session_mode": "mission"},
    )
    for key, val in overrides.items():
        if key == "input_payload":
            payload = dict(state.get("input_payload") or {})
            payload.update(val)
            state = merge_state(state, input_payload=payload)
        else:
            state = merge_state(state, **{key: val})
    return state


# --- R4: continue after steer must not raise awaits supersede ---


def test_message_stream_bootstraps_new_session(base_state):
    """First message to unknown session_id creates task (fixes debug.log 404)."""
    session_id = "bootstrap-session-001"
    ctrl = SessionController()
    events = list(
        ctrl.handle_message(
            session_id,
            "你好",
            user_id="tester",
        )
    )
    assert any("stream_open" in e or "task_created" in e for e in events)
    stored = get_state_store().load(session_id)
    assert stored is not None
    payload = stored.get("input_payload") or {}
    assert "你好" in str(payload.get("goal") or "")


def test_greeting_without_preempt_is_new_task_not_interrupt(base_state):
    event = classify_user_event(
        base_state,
        payload={"goal": "你好", "message": "你好"},
    )
    assert event.event_type == "new_task"

    # Stale frontend preempt flags must not hijack idle QA turns.
    idle_preempt = classify_user_event(
        base_state,
        payload={"goal": "你好", "message": "你好", "preempt": True, "replace_goal": True},
    )
    assert idle_preempt.event_type == "new_task"


def test_sync_fsm_repairs_replanning_when_legacy_flags_set(base_state):
    state = merge_state(
        base_state,
        input_payload=apply_steer_planning_gate({"goal": "x", "fsm_state": "RUNNING"}),
        fsm_state="RUNNING",
    )
    from app.services.session_fsm import sync_fsm_state

    fixed = sync_fsm_state(state)
    assert get_fsm_state(fixed) == FSM_REPLANNING


def test_r4_resume_while_replanning_auto_supersede(base_state):
    state = _writing_mission_state(
        base_state,
        input_payload=apply_steer_planning_gate({"goal": "写同人"}),
    )
    state = mark_supersede_replan_queued(state)
    state = set_fsm_state(state, FSM_REPLANNING)
    get_state_store().save(state)

    runner = GraphRunner()
    prepared = runner.prepare_resume_mission(state["task_id"])
    payload = prepared.get("input_payload") or {}
    assert payload.get("foreground_replan_dispatch") is True
    assert "execution_grant" not in payload


def test_r4_classify_resume_under_replanning_becomes_redirect(base_state):
    state = _writing_mission_state(
        base_state,
        input_payload=apply_steer_planning_gate({"goal": "写同人", "fsm_state": "REPLANNING"}),
    )
    state = mark_supersede_replan_queued(state)
    from app.services.session_controller import _resolve_inbound_dispatch

    _, dispatched = _resolve_inbound_dispatch(state, {"goal": "继续", "message": "继续"})
    assert dispatched.event_type == "redirect"


# --- R10: idempotent message submit ---


def test_r10_duplicate_client_message_id(base_state):
    state = _writing_mission_state(
        base_state,
        input_payload={"goal": "test", "last_applied_message_id": "msg-001"},
    )
    get_state_store().save(state)
    ctrl = SessionController()
    events = list(
        ctrl.handle_message(
            state["task_id"],
            "hello",
            client_message_id="msg-001",
        )
    )
    assert any("idempotent" in e for e in events)


# --- R11: illegal FSM transition rejected ---


def test_r11_illegal_fsm_transition():
    with pytest.raises(InvariantViolation, match="I11"):
        validate_fsm_transition("IDLE", "INVALID")


def test_r11_invariant_guard_auto_repairs_i1_mismatch(base_state, monkeypatch):
    """I1 mismatch (IDLE + replan flags) is repaired via sync_fsm_state before save."""
    monkeypatch.setenv("INVARIANT_GUARD_STRICT", "1")
    payload = apply_steer_planning_gate({"goal": "x", "fsm_state": "IDLE"})
    state = merge_state(base_state, input_payload=payload, fsm_state="IDLE")
    fixed = validate(state)
    from app.services.session_fsm import FSM_REPLANNING, get_fsm_state

    assert get_fsm_state(fixed) == FSM_REPLANNING


# --- R9: cancelled run rejects writes ---


def test_r9_cancelled_run_blocks_assert(base_state):
    state = merge_state(
        base_state,
        execution_run={"run_id": "r1", "cancelled": True, "revision_at_start": 0},
        input_payload={"intent_revision": 1},
    )
    with pytest.raises(RunCancelled):
        RunController.assert_run_active(state)


def test_r9_cancel_bumps_revision(base_state):
    state = merge_state(
        base_state,
        input_payload={"intent_revision": 1},
        execution_run={"run_id": "abc"},
    )
    get_state_store().save(state)
    updated = RunController.cancel(state, reason="steer")
    assert int((updated.get("input_payload") or {}).get("intent_revision") or 0) >= 2
    assert (updated.get("execution_run") or {}).get("cancelled") is True


def test_r9_cancelled_run_rejects_step_committer(base_state):
    from app.services.run_controller import RunCancelled
    from app.services.step_committer import StepCommitter

    state = merge_state(
        base_state,
        execution_run={"run_id": "r9", "cancelled": True, "revision_at_start": 0},
        input_payload={"intent_revision": 1},
    )
    committer = StepCommitter(
        state,
        step_id="s1",
        kind="write_outline",
        filename="outline.txt",
    )
    with pytest.raises(RunCancelled):
        committer.check_control(phase="pre_commit")


def test_r9_cancelled_run_rejects_artifact_write(base_state, test_settings, monkeypatch):
    import app.services.artifact_tools as art_mod
    from app.services.artifact_tools import handle_write_text_artifact
    from app.services.run_controller import RunCancelled

    monkeypatch.setattr(art_mod.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    state = merge_state(
        base_state,
        task_id="r9-artifact",
        execution_run={"run_id": "r9", "cancelled": True, "revision_at_start": 0},
        input_payload={"intent_revision": 1},
    )
    with pytest.raises(RunCancelled):
        handle_write_text_artifact(
            {
                "task_id": "r9-artifact",
                "filename": "blocked.txt",
                "content": "should not persist",
                "_agent_state": state,
            }
        )


def test_r9_cancelled_run_leaves_disk_unchanged(base_state, test_settings, monkeypatch):
    """R9: cancelled run must not persist new artifact bytes."""
    from pathlib import Path

    import app.services.artifact_tools as art_mod
    from app.services.artifact_tools import handle_write_text_artifact
    from app.services.run_controller import RunCancelled

    monkeypatch.setattr(art_mod.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "r9-disk"
    target = Path(test_settings.ARTIFACTS_PATH) / task_id / "blocked.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    state = merge_state(
        base_state,
        task_id=task_id,
        execution_run={"run_id": "r9", "cancelled": True, "revision_at_start": 0},
        input_payload={"intent_revision": 1},
    )
    with pytest.raises(RunCancelled):
        handle_write_text_artifact(
            {
                "task_id": task_id,
                "filename": "blocked.txt",
                "content": "stale run bytes",
                "_agent_state": state,
            }
        )
    assert not target.exists()


# --- R2/R5: redirect path uses single classify authority ---


def test_r2_redirect_signal_on_steer_text(base_state):
    state = _writing_mission_state(base_state)
    event = classify_user_event(
        state,
        payload={"event_type": "redirect", "goal": "改成原电影人物"},
    )
    assert event.event_type == "redirect"
    assert event.source == "explicit"


def test_r2_edit_plot_contract_routes_to_tool_execution(base_state):
    """R2: steer replan with edit_plot must not shortcut past tool_execution."""
    from app.runtime.planning_gate_router import route_after_incremental_planning
    from app.runtime.state import TaskStatus

    state = merge_state(
        base_state,
        status=TaskStatus.PLANNED.value,
        skip_retrieval=True,
        input_payload={
            "turn_contract": {
                "primary_op": "edit_plot",
                "ops": ["read_text_artifact"],
            },
            "selected_tools": ["read_text_artifact"],
            "fsm_state": "REPLANNING",
        },
    )
    assert route_after_incremental_planning(state) == "tool_execution"


def test_r2_edit_plot_executed_step_allows_finalize(base_state):
    """R2: after tool/writing execute, edit_plot turn may finalize."""
    from app.services.turn_guard import can_finalize_turn, mark_turn_step_executed

    state = merge_state(
        base_state,
        input_payload={
            "turn_contract": {"primary_op": "edit_plot", "ops": ["read_text_artifact"]},
            "fsm_state": "RUNNING",
        },
    )
    allowed_before, reason_before = can_finalize_turn(state)
    assert not allowed_before
    assert "edit_plot" in reason_before or reason_before == "unexecuted_contract"
    executed = mark_turn_step_executed(state)
    allowed, reason = can_finalize_turn(executed)
    assert allowed
    assert reason in ("executed", "ok")


def test_r9_steer_cancel_blocks_stale_run_write(base_state, test_settings, monkeypatch):
    """R9: cancel during active run — stale execution_run cannot persist bytes."""
    from pathlib import Path

    import app.services.artifact_tools as art_mod
    from app.services.artifact_tools import handle_write_text_artifact
    from app.services.run_controller import RunCancelled, RunController

    monkeypatch.setattr(art_mod.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    task_id = "r9-steer-cancel"
    state = merge_state(
        base_state,
        task_id=task_id,
        execution_run={"run_id": "active-run", "revision_at_start": 0, "cancelled": False},
        input_payload={"intent_revision": 1, "fsm_state": "RUNNING"},
    )
    get_state_store().save(state)
    cancelled = RunController.cancel(state, reason="steer")
    assert (cancelled.get("execution_run") or {}).get("cancelled") is True
    target = Path(test_settings.ARTIFACTS_PATH) / task_id / "stale.txt"
    with pytest.raises(RunCancelled):
        handle_write_text_artifact(
            {
                "task_id": task_id,
                "filename": "stale.txt",
                "content": "from cancelled run",
                "_agent_state": cancelled,
            }
        )
    assert not target.exists()


# --- 止血: accessor + read guard + grounding split ---


def test_hemostasis_accessor_read_guard_grounding(base_state, test_settings, monkeypatch):
    from app.runtime.state_field_access import mission_from_state
    from app.services.artifact_read_guard import block_repeat_artifact_read
    from app.services.grounding_policy import grounding_hits_for_state, should_run_grounding_check

    mission = {"kind": "writing", "step_policy": {"first_step": "outline"}}
    state = merge_state(
        base_state,
        mission=mission,
        input_payload={"mission": mission},
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"filename": "novel.txt", "content": "x", "status": "ok"},
            },
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"filename": "novel.txt", "content": "x", "status": "ok"},
            },
        ],
        reasoning_result={"summary": "根据读取，剧情继续。"},
        skip_retrieval=True,
    )
    assert mission_from_state(state) == mission
    import app.services.artifact_read_guard as guard

    monkeypatch.setattr(guard.settings, "ARTIFACT_MAX_READS_SAME_FILE", 2)
    assert block_repeat_artifact_read(state, filename="novel.txt") is not None
    assert should_run_grounding_check(state) is True
    mode, hits = grounding_hits_for_state(state)
    assert mode == "tool_observation"
    assert hits


# --- R8: chat mode does not imply mission append ---


def test_r8_chat_mode_session(base_state):
    state = merge_state(
        base_state,
        input_payload={"goal": "追问", "session_mode": "chat"},
    )
    from app.services.session_fsm import session_mode

    assert session_mode(state) == "chat"
    assert not state.get("mission")


# --- Phase C: turn guard ---


def test_turn_guard_blocks_unexecuted_edit_contract(base_state):
    state = merge_state(
        base_state,
        status=TaskStatus.COMPLETED.value,
        input_payload={
            "turn_contract": {"primary_op": "edit_plot"},
            "fsm_state": "RUNNING",
        },
    )
    allowed, reason = can_finalize_turn(state)
    assert not allowed
    assert "edit_plot" in reason or reason == "unexecuted_contract"


def test_planning_gate_contract_forces_tool_route():
    payload = {"turn_contract": {"primary_op": "edit_plot", "ops": ["read_text_artifact"]}}
    assert contract_requires_execution_route(payload) is True


# --- FSM derivation from legacy flags ---


def test_fsm_derives_replanning_from_steer_gate(base_state):
    state = merge_state(
        base_state,
        input_payload=apply_steer_planning_gate({"goal": "x"}),
    )
    assert derive_fsm_from_legacy(state) == FSM_REPLANNING
    assert get_fsm_state(state) == FSM_REPLANNING


# --- R1: empty disk → write_outline, not append ---


def test_r1_empty_disk_coerces_write_outline(base_state, test_settings, monkeypatch):
    import app.services.artifact_tools as art
    from app.services.writing_step import (
        coerce_writing_action_for_manuscript_state,
        materialize_writing_step_intent,
    )

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    state = merge_state(
        base_state,
        manuscript={"body_bytes": 0, "outline_bytes": 0},
        mission={"kind": "writing", "step_policy": {"first_step": "outline"}},
    )
    assert coerce_writing_action_for_manuscript_state(state, "append_body") == "write_outline"
    intent = materialize_writing_step_intent(state, state.get("mission") or {})
    assert intent["action"] == "write_outline"


# --- R3: no outline steer → write_outline ---


def test_r3_steer_replan_without_outline_targets_write_outline(base_state):
    from app.services.turn_contract import steer_replan_outline_plan

    plan = steer_replan_outline_plan({"manuscript": {"outline_bytes": 0}}, steer="改剧情")
    assert plan.get("writing_intent", {}).get("action") == "write_outline"
    assert plan.get("mission_intervention", {}).get("action") == "rewrite_outline"


# --- R5: WRITTEN status steer queues supersede without error ---


def test_r5_written_steer_queues_supersede(base_state):
    from app.services.mission_steer import queue_steer_message
    from app.services.mission_supersede import supersede_replan_queued

    mission = build_mission_dict(
        base_state,
        {"mission": {"kind": "writing"}},
        kind="writing",
    )
    state = merge_state(
        base_state,
        mission=mission,
        status=TaskStatus.WRITTEN.value,
        input_payload={"goal": "写小说", "session_mode": "mission"},
    )
    get_state_store().save(state)
    updated = queue_steer_message(state["task_id"], "改剧情走向", preempt=True, replace_goal=True)
    assert updated.get("status") == TaskStatus.MISSION_PAUSED.value
    assert supersede_replan_queued(updated)


# --- R6: repeated read capped ---


def test_r6_repeat_read_blocked(base_state, test_settings, monkeypatch):
    import app.services.artifact_read_guard as guard
    from app.services.artifact_read_guard import block_repeat_artifact_read

    monkeypatch.setattr(guard.settings, "ARTIFACT_MAX_READS_SAME_FILE", 2)
    state = merge_state(
        base_state,
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"filename": "novel.txt", "content": "cached", "status": "ok"},
            },
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"filename": "novel.txt", "content": "cached", "status": "ok"},
            },
        ],
    )
    blocked = block_repeat_artifact_read(state, filename="novel.txt")
    assert blocked is not None
    assert blocked.get("status") == "cached"


# --- R7: tool turn summary not killed by writing faithfulness ---


def test_r7_tool_turn_passes_output_guard(base_state, monkeypatch):
    from app.nodes.output_guard_node import output_guard_node

    monkeypatch.setattr("app.nodes.output_guard_node.settings.OUTPUT_GUARD_ENABLED", True)
    state = merge_state(
        base_state,
        skip_retrieval=True,
        tool_results=[
            {
                "tool": "read_text_artifact",
                "status": "ok",
                "result": {"content": "主角在雨夜逃脱", "status": "ok"},
            }
        ],
        reasoning_result={
            "summary": "根据读取结果，主角在雨夜逃脱。",
            "confidence": 0.9,
            "risk_level": "LOW",
        },
        input_payload={
            **base_state["input_payload"],
            "turn_contract": {"primary_op": "edit_plot"},
            "goal": "优化结局",
        },
        status=TaskStatus.REASONED.value,
    )
    result = output_guard_node(state)
    guard = result.get("output_guard_result") or {}
    assert guard.get("passed") is True
    assert result.get("status") != TaskStatus.REJECTED.value

