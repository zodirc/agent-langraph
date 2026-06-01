"""Align AgentState manuscript/progress with on-disk artifacts (long writes, debug API)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, merge_state
from app.services.manuscript_service import resolve_manuscript
from app.services.state_store import get_state_store


def enrich_agent_state_manuscript(state: AgentState) -> AgentState:
    """
    Refresh manuscript bytes and chapter indices from artifact files.

    Disk is authoritative when ahead of stored state (common during long mission_act).
    """
    task_id = str(state["task_id"])
    ms_dict = resolve_manuscript(task_id, state.get("manuscript")).to_dict()

    progress = dict(state.get("progress") or {})
    metrics = dict(progress.get("metrics") or {})
    metrics["last_chapter_index"] = int(ms_dict.get("last_chapter_index") or 0)
    metrics["body_bytes"] = int(ms_dict.get("body_bytes") or 0)
    metrics["body_path"] = ms_dict.get("body_path")
    metrics["chapter_cursor"] = int(ms_dict.get("chapter_cursor") or 0)
    progress["metrics"] = metrics
    progress["disk_synced_at"] = _now_iso()

    return merge_state(state, manuscript=ms_dict, progress=progress)


def checkpoint_writing_state(
    state: AgentState,
    *,
    tool_results: list[dict[str, Any]] | None = None,
) -> AgentState:
    """Persist in-flight manuscript progress so Web /state and /status stay current."""
    updated = enrich_agent_state_manuscript(state)
    if tool_results is not None:
        updated = merge_state(updated, tool_results=tool_results)
    updated = merge_state(updated, current_node="writing")
    get_state_store().save(updated)
    return updated


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
