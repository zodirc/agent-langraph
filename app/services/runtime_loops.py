"""Foreground / background runtime loop helpers (WP-1.5)."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from app.runtime.state import AgentState, merge_state

FOREGROUND_NODES = frozenset(
    {"event_classification", "acknowledge", "interrupt_control", "incremental_planning"}
)
BACKGROUND_NODES = frozenset(
    {
        "retrieval",
        "tool_execution",
        "engineering_execution",
        "context_governance",
        "reasoning_or_writing",
        "verification",
        "policy",
        "output",
        "memory_writeback",
        "eval_capture",
    }
)
PROCESS_EVENT_NODES = frozenset({"retrieval", "tool_execution", "verification"})


def classify_runtime_loop(node_name: str) -> str:
    if node_name in FOREGROUND_NODES:
        return "foreground"
    if node_name in BACKGROUND_NODES:
        return "background"
    return "unknown"


def stamp_runtime_loops(state: AgentState, node_name: str) -> AgentState:
    loop = classify_runtime_loop(node_name)
    if loop == "foreground":
        fg = dict(state.get("foreground_status") or {})
        fg.update({"phase": node_name, "last_node": node_name, "loop": "foreground"})
        return merge_state(state, foreground_status=fg)
    if loop == "background":
        bg = dict(state.get("background_status") or {})
        bg.update({"phase": node_name, "last_node": node_name, "loop": "background", "active": True})
        return merge_state(state, background_status=bg)
    return state


def emit_process_events(
    node_name: str,
    state: AgentState,
    emit: Callable[[str, dict[str, Any]], None],
) -> None:
    """Emit retrieval/tool/verification process events without blocking foreground ACK."""
    if node_name not in PROCESS_EVENT_NODES:
        return
    emit(
        "process",
        {
            "task_id": state.get("task_id"),
            "phase": node_name,
            "status": state.get("status"),
            "loop": "background",
        },
    )


def run_foreground_then_background(
    state: AgentState,
    foreground_steps: Iterable[tuple[str, AgentState]],
    background_steps: Iterable[tuple[str, AgentState]],
) -> AgentState:
    """Test helper: apply foreground steps first, then background steps."""
    latest = state
    fg_status = None
    bg_status = None
    for node_name, snap in foreground_steps:
        stamped = stamp_runtime_loops(snap, node_name)
        fg_status = stamped.get("foreground_status")
        latest = stamped
    for node_name, snap in background_steps:
        stamped = stamp_runtime_loops(snap, node_name)
        bg_status = stamped.get("background_status")
        latest = stamped
    return merge_state(
        latest,
        foreground_status=fg_status,
        background_status=bg_status,
    )
