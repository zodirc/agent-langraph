"""Backward-compatible facade — prefer app.services.manuscript_service."""

from __future__ import annotations

from typing import Any

from app.services.manuscript_service import (
    Manuscript,
    enrich_payload,
    is_continue_writing_goal,
    resolve_manuscript as resolve_session_artifacts,
    resolve_read_paths,
    split_execution_tools,
)

# Legacy alias
SessionArtifacts = Manuscript


def enrich_payload_with_session_artifacts(
    payload: dict[str, Any],
    task_id: str,
    *,
    session_turn: int = 1,
) -> dict[str, Any]:
    return enrich_payload(payload, task_id, session_turn=session_turn)


def normalize_writing_tool_params(
    payload: dict[str, Any],
    selected_tools: list[str],
    tool_params: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Legacy: split writing tools; content stripping happens in planning."""
    from app.services.manuscript_service import build_writing_intent

    exec_tools, writing_tools = split_execution_tools(selected_tools)
    ms = resolve_session_artifacts(task_id=payload.get("task_id") or "")
    goal = str(payload.get("goal") or "")
    intent = build_writing_intent(
        goal=goal,
        selected_tools=writing_tools or selected_tools,
        manuscript=ms,
        session_turn=int(payload.get("session_turn") or 1),
    )
    if intent.get("enabled"):
        payload = {**payload, "writing_intent": intent}
    slim = {k: v for k, v in tool_params.items() if k not in writing_tools}
    return exec_tools, slim
