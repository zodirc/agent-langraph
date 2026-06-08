"""Tool observation commit gate (WP-4.3)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState
from app.services.foreground_execution import get_foreground_epoch


def filter_tool_observations_for_commit(
    state: AgentState,
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop stale parallel observations (execution_version / foreground epoch)."""
    expected_version = state.get("execution_version")
    expected_epoch = get_foreground_epoch(state)
    ctx = state.get("interrupt_context") or {}
    if str(ctx.get("runtime_state") or "") in {"INTERRUPTED", "ABORTED"}:
        return []

    committed: list[dict[str, Any]] = []
    for row in observations:
        if not isinstance(row, dict):
            continue
        row_version = row.get("execution_version")
        if expected_version is not None and row_version is not None:
            if int(row_version) != int(expected_version):
                continue
        row_epoch = row.get("foreground_epoch")
        if row_epoch is not None and int(row_epoch) < int(expected_epoch):
            continue
        committed.append(row)
    return committed


def merge_observations_to_tool_results(
    state: AgentState,
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Commit gate: observation buffer → canonical tool_results."""
    filtered = filter_tool_observations_for_commit(state, observations)
    prior = list(state.get("tool_results") or [])
    names = {str(r.get("tool")) for r in prior if isinstance(r, dict)}
    merged = list(prior)
    for row in filtered:
        tool = str(row.get("tool") or "")
        if tool and tool not in names:
            merged.append(row)
            names.add(tool)
        elif tool:
            merged = [r for r in merged if str(r.get("tool")) != tool] + [row]
    return merged
