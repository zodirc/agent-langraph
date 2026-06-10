"""Optimization PASS gate tests — closes audit gaps for WP 1.3–4.3."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.runtime.router as router_module
from app.runtime.planning_gate_router import route_after_incremental_planning
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.interrupt_control import (
    RUNTIME_INTERRUPTED,
    RUNTIME_INTERRUPT_REQUESTED,
    RUNTIME_RUNNING,
    apply_interrupt_control,
)
from app.services.runtime_loops import (
    BACKGROUND_NODES,
    FOREGROUND_NODES,
    PROCESS_EVENT_NODES,
    classify_runtime_loop,
    emit_process_events,
    run_foreground_then_background,
    stamp_runtime_loops,
)
from app.services.structured_checkpoint import (
    build_structured_checkpoint,
    checkpoint_recovery_metrics,
    is_checkpoint_expired,
    restore_from_structured_checkpoint,
)
from app.services.tool_commit_gate import filter_tool_observations_for_commit
from app.services.tool_side_effect import normalize_stages_for_safe_parallel, tool_is_read_only


# --- WP-4.2 ---
def test_route_after_planning_removed_from_router():
    assert not hasattr(router_module, "route_after_planning")


def test_legacy_guard_routes_removed_from_router():
    assert not hasattr(router_module, "route_after_policy_to_guard")
    assert not hasattr(router_module, "route_after_output_guard")
    assert not hasattr(router_module, "route_after_writing")


def test_legacy_react_loop_folded_into_plan_graph_meta():
    state = merge_state(create_initial_state(), react_loop={"round": 1})
    meta = (state.get("plan_graph") or {}).get("meta") or {}
    assert meta.get("react_loop") == {"round": 1}
    assert state.get("react_loop") is None


# --- WP-1.4 ---
def test_incremental_planning_router_is_canonical_post_plan_fork():
    state = merge_state(
        create_initial_state(input_payload={"goal": "hi"}),
        plan=["reason_and_answer"],
        skip_retrieval=True,
        selected_tools=[],
    )
    assert route_after_incremental_planning(state) == "context_governance"


# --- WP-1.3 ---
@pytest.mark.parametrize(
    "phase_node",
    ["incremental_planning", "retrieval", "tool_execution", "reasoning_or_writing"],
)
def test_interrupt_at_phase_marks_replanning(phase_node):
    state = merge_state(
        create_initial_state(input_payload={"goal": "stop mid task"}),
        event_type="interrupt",
        current_node=phase_node,
        background_status={"phase": phase_node},
    )
    updated = apply_interrupt_control(state)
    ctx = updated.get("interrupt_context") or {}
    assert ctx.get("runtime_state") in {RUNTIME_INTERRUPT_REQUESTED, RUNTIME_INTERRUPTED}


def test_interrupt_aborted_boundary():
    state = merge_state(
        create_initial_state(),
        event_type="interrupt",
        interrupt_context={"abort_requested": True, "cancel_requested": True},
    )
    updated = apply_interrupt_control(state)
    assert updated["status"] == TaskStatus.CANCELLED.value


def test_interrupt_resume_restores_running():
    ckpt = build_structured_checkpoint(
        merge_state(create_initial_state(), execution_version=2, plan=["a"])
    )
    state = merge_state(
        create_initial_state(input_payload={"structured_checkpoint": ckpt}),
        event_type="resume",
    )
    updated = apply_interrupt_control(state)
    ctx = updated.get("interrupt_context") or {}
    assert ctx.get("runtime_state") in {"RESUMABLE_PLAN_READY", RUNTIME_RUNNING}


# --- WP-1.5 ---
def test_foreground_runs_before_background_in_helper():
    fg = [("acknowledge", merge_state(create_initial_state(), current_node="acknowledge"))]
    bg = [("retrieval", merge_state(create_initial_state(), current_node="retrieval"))]
    out = run_foreground_then_background(create_initial_state(), fg, bg)
    assert (out.get("foreground_status") or {}).get("phase") == "acknowledge"
    assert (out.get("background_status") or {}).get("phase") == "retrieval"


def test_three_process_event_types():
    assert PROCESS_EVENT_NODES >= {"retrieval", "tool_execution", "verification"}


def test_emit_process_events_for_background_nodes():
    events: list[tuple[str, dict]] = []

    def capture(kind, payload):
        events.append((kind, payload))

    for node in ("retrieval", "tool_execution", "verification"):
        emit_process_events(node, create_initial_state(), capture)
    assert len(events) == 3
    assert all(row[0] == "process" for row in events)


def test_runtime_loop_classification():
    assert classify_runtime_loop("acknowledge") == "foreground"
    assert classify_runtime_loop("tool_execution") == "background"
    assert "acknowledge" in FOREGROUND_NODES
    assert "verification" in BACKGROUND_NODES


# --- WP-4.1 ---
def test_checkpoint_ttl_expired():
    ckpt = build_structured_checkpoint(create_initial_state())
    old = datetime.now(timezone.utc) - timedelta(days=30)
    ckpt["created_at"] = old.isoformat()
    assert is_checkpoint_expired(ckpt, now=datetime.now(timezone.utc)) is True


def test_checkpoint_recovery_metrics():
    metrics = checkpoint_recovery_metrics(
        [{"restored": True}, {"restored": False}, {"restored": True}]
    )
    assert metrics["attempts"] == 3
    assert metrics["successes"] == 2
    assert metrics["success_rate"] == pytest.approx(2 / 3)


def test_checkpoint_resume_roundtrip_with_version():
    base = merge_state(create_initial_state(), execution_version=4, plan=["s"])
    ckpt = build_structured_checkpoint(base)
    restored = restore_from_structured_checkpoint(
        merge_state(base, input_payload={"structured_checkpoint": ckpt, "resume": True})
    )
    assert restored.get("execution_version") == 4
    recovery = (restored.get("background_status") or {}).get("checkpoint_recovery") or {}
    assert recovery.get("successes") == 1
    assert recovery.get("attempts") == 1


def test_checkpoint_restore_metrics_wired_to_eval_capture():
    from app.nodes.eval_capture_node import eval_capture_node
    from app.services.metrics_service import get_metrics_service

    base = merge_state(create_initial_state(), execution_version=2, plan=["s"])
    ckpt = build_structured_checkpoint(base)
    restored = restore_from_structured_checkpoint(
        merge_state(base, input_payload={"structured_checkpoint": ckpt})
    )
    captured = eval_capture_node(
        merge_state(restored, status=TaskStatus.COMPLETED.value, final_answer="ok")
    )
    assert (captured.get("eval_capture") or {}).get("checkpoint_recovery", {}).get("successes") == 1
    counters = get_metrics_service().summary()["counters"]
    assert counters.get("structured_checkpoint_restore_success", 0) >= 1


# --- WP-4.3 five principles ---
def test_principle1_single_write_entry_via_merge_state():
    s1 = merge_state(create_initial_state(), execution_version=1)
    s2 = merge_state(s1, execution_version=2)
    assert s2.get("execution_version") == 2


def test_principle2_observation_commit_gate_only():
    state = merge_state(create_initial_state(), execution_version=1)
    obs = [{"tool": "echo", "status": "ok", "execution_version": 1}]
    assert len(filter_tool_observations_for_commit(state, obs)) == 1


def test_principle3_interrupt_cancels_parallel():
    state = merge_state(
        create_initial_state(),
        interrupt_context={"runtime_state": "INTERRUPTED"},
    )
    assert filter_tool_observations_for_commit(state, [{"tool": "echo", "execution_version": 1}]) == []


def test_principle4_stale_version_dropped():
    state = merge_state(create_initial_state(), execution_version=3)
    obs = [{"tool": "echo", "execution_version": 2}]
    assert filter_tool_observations_for_commit(state, obs) == []


def test_principle5_writes_not_parallelized():
    stages = normalize_stages_for_safe_parallel([["read_file", "write_file", "echo"]])
    assert not any("write_file" in s and len(s) > 1 for s in stages)
    assert all(len(s) == 1 or all(tool_is_read_only(t) for t in s) for s in stages)


def test_principle_integration_tool_commit_on_execution_path():
    """Integration: tool observations commit only through gate on execution path."""
    observations = [{"tool": "echo", "execution_version": 5, "status": "ok"}]
    state = merge_state(
        create_initial_state(input_payload={"goal": "echo test"}),
        execution_version=5,
        selected_tools=["echo"],
        plan=["tool_then_answer"],
    )
    interrupted = merge_state(
        state,
        interrupt_context={"runtime_state": RUNTIME_INTERRUPTED},
    )
    assert filter_tool_observations_for_commit(interrupted, observations) == []
    assert len(filter_tool_observations_for_commit(state, observations)) == 1
    stale = [{"tool": "echo", "execution_version": 4, "status": "ok"}]
    assert filter_tool_observations_for_commit(state, stale) == []
    stages = normalize_stages_for_safe_parallel([["echo", "read_file"]])
    assert stages == [["echo", "read_file"]]
