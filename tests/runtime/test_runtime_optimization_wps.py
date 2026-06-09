"""Runtime optimization WP tests (1.3–4.3)."""

from __future__ import annotations

import pytest

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.context_budget import BUDGET_BUCKET_NAMES, initialize_context_budget_buckets
from app.services.incremental_planning import analyze_plan_impact, apply_plan_invalidations
from app.services.interrupt_control import apply_interrupt_control, should_abort_after_interrupt
from app.services.structured_checkpoint import build_structured_checkpoint, restore_from_structured_checkpoint
from app.services.tool_commit_gate import filter_tool_observations_for_commit, merge_observations_to_tool_results
from app.services.tool_side_effect import normalize_stages_for_safe_parallel, tool_is_read_only


# --- WP-1.3 interrupt ---
@pytest.mark.parametrize(
    "event_type,extra,expect_abort,expect_replan",
    [
        ("interrupt", {}, False, True),
        ("new_task", {}, False, False),
        ("resume", {"resume_checkpoint_ref": "ckpt-1"}, False, False),
    ],
)
def test_interrupt_control_routes(event_type, extra, expect_abort, expect_replan):
    state = create_initial_state(input_payload={"goal": "test", **extra})
    state = merge_state(state, event_type=event_type)
    updated = apply_interrupt_control(state)
    if expect_abort:
        assert should_abort_after_interrupt(updated)
    ctx = updated.get("interrupt_context") or {}
    if expect_replan:
        assert ctx.get("runtime_state") in {"REPLANNING", "INTERRUPTED"}
    if event_type == "resume":
        assert ctx.get("runtime_state") == "RESUMABLE_PLAN_READY"


def test_interrupt_aborted():
    state = create_initial_state(input_payload={"goal": "stop"})
    state = merge_state(
        state,
        event_type="interrupt",
        interrupt_context={"abort_requested": True, "cancel_requested": True},
    )
    updated = apply_interrupt_control(state)
    assert should_abort_after_interrupt(updated)
    assert updated["status"] == TaskStatus.CANCELLED.value


# --- WP-1.4 incremental planning ---
def test_local_plan_invalidation_file_scope():
    state = create_initial_state(
        input_payload={"goal": "改 B 文件", "target_files": ["b.txt"], "event_type": "clarification"},
    )
    state = merge_state(state, event_type="clarification", plan=["edit a.txt", "edit b.txt"])
    impact = analyze_plan_impact(state)
    graph = apply_plan_invalidations(
        {
            "nodes": [
                {"id": "1", "description": "edit a.txt", "status": "valid"},
                {"id": "2", "description": "edit b.txt", "status": "valid"},
            ]
        },
        impact["invalidations"],
    )
    statuses = {n["description"]: n["status"] for n in graph["nodes"]}
    assert statuses["edit a.txt"] == "valid"
    assert statuses["edit b.txt"] == "must_rerun"


def test_clarification_without_target_files_marks_stale():
    state = create_initial_state(input_payload={"goal": "clarify"})
    state = merge_state(state, event_type="clarification", plan=["step1"])
    graph = apply_plan_invalidations(
        {"nodes": [{"id": "1", "description": "step1", "status": "valid"}]},
        analyze_plan_impact(state)["invalidations"],
    )
    assert graph["nodes"][0]["status"] == "stale"


# --- WP-1.5 foreground/background ---
def test_execution_phase_stamping():
    from app.services.graph_runner import _apply_execution_phase_status

    state = create_initial_state()
    fg = _apply_execution_phase_status(state, "acknowledge")
    assert (fg.get("foreground_status") or {}).get("phase") == "acknowledge"
    bg = _apply_execution_phase_status(state, "tool_execution")
    assert (bg.get("background_status") or {}).get("phase") == "tool_execution"


# --- WP-2.2 layered compression ---
def test_compression_class_mapping():
    from app.services.context_items import ContextItem, new_context_id
    from app.services.context_reducer import compression_class_for_item

    tool_item = ContextItem(
        id=new_context_id(),
        kind="tool_output",
        source="tool",
        content="[echo] ok: hi",
        bucket="tool_observations",
    )
    assert compression_class_for_item(tool_item) == "tool"


# --- WP-3.1 verification ---
def test_verification_submission_decision_submit_without_reflection_routes():
    from app.nodes.verification_node import _submission_decision

    state = merge_state(
        create_initial_state(),
        reflection_result={"route": "retry_reasoning"},
        status=TaskStatus.REASONED.value,
    )
    assert _submission_decision(state)["decision"] == "submit"


# --- WP-3.2 eval capture ---
def test_eval_capture_eligible_on_positive_feedback():
    from app.nodes.eval_capture_node import eval_capture_node

    state = merge_state(
        create_initial_state(input_payload={"goal": "x", "user_feedback": "positive"}),
        status=TaskStatus.COMPLETED.value,
        final_answer="done",
    )
    out = eval_capture_node(state)
    assert (out.get("eval_capture") or {}).get("eligible") is True


# --- WP-4.3 parallel principles ---
def test_parallel_principles_interrupt_drops_observations():
    state = merge_state(
        create_initial_state(),
        execution_version=2,
        interrupt_context={"runtime_state": "INTERRUPTED"},
    )
    obs = [{"tool": "echo", "status": "ok", "execution_version": 2}]
    assert filter_tool_observations_for_commit(state, obs) == []


def test_parallel_principles_read_only_batch():
    from app.services.tool_side_effect import partition_stage_by_side_effect

    stages = partition_stage_by_side_effect(["echo", "calculator", "write_file"])
    assert stages[0] == ["echo", "calculator"]
    assert stages[1] == ["write_file"]


def test_seven_budget_buckets_initialized():
    state = create_initial_state()
    buckets = initialize_context_budget_buckets(state)
    assert set(buckets["caps"].keys()) == set(BUDGET_BUCKET_NAMES)
    assert buckets["soft_limit"] < buckets["hard_limit"] <= buckets["emergency_limit"]


# --- WP-4.1 checkpoint ---
def test_structured_checkpoint_roundtrip():
    state = create_initial_state(input_payload={"goal": "long task"})
    state = merge_state(
        state,
        execution_version=3,
        plan=["step1"],
        tool_results=[{"tool": "echo", "status": "ok"}],
    )
    ckpt = build_structured_checkpoint(state)
    restored = restore_from_structured_checkpoint(
        merge_state(
            state,
            input_payload={"goal": "long task", "structured_checkpoint": ckpt, "resume": True},
        )
    )
    assert restored.get("execution_version") == 3
    assert restored.get("tool_results")


# --- WP-4.3 tool safe parallel ---
def test_write_tools_not_parallelized_with_reads():
    stages = normalize_stages_for_safe_parallel([["echo", "write_file", "calculator"]])
    assert all(len(s) == 1 or all(tool_is_read_only(t) for t in s) for s in stages)


def test_tool_commit_gate_drops_stale_version():
    state = merge_state(create_initial_state(), execution_version=2)
    obs = [
        {"tool": "echo", "status": "ok", "execution_version": 1},
        {"tool": "calc", "status": "ok", "execution_version": 2},
    ]
    filtered = filter_tool_observations_for_commit(state, obs)
    assert len(filtered) == 1
    assert filtered[0]["tool"] == "calc"


def test_tool_commit_gate_merge():
    state = merge_state(create_initial_state(), execution_version=1)
    merged = merge_observations_to_tool_results(
        state,
        [{"tool": "echo", "status": "ok", "execution_version": 1}],
    )
    assert len(merged) == 1


def test_graph_spine_includes_core_nodes():
    from app.runtime.graph import build_agent_graph

    graph = build_agent_graph().compile().get_graph()
    nodes = set(graph.nodes.keys())
    for name in (
        "event_classification",
        "acknowledge",
        "interrupt_control",
        "incremental_planning",
        "context_governance",
        "reasoning_or_writing",
        "verification",
        "output",
    ):
        assert name in nodes
    assert any(
        edge.source == "output" and edge.target == "__end__" for edge in graph.edges
    )
