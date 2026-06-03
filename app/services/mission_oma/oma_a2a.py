"""A2A dispatch for manuscript-bound OMAW workers (ADR 4.2)."""

from __future__ import annotations

from typing import Any

from app.domain.agent_message import AgentMessage
from app.runtime.state import AgentState, merge_state
from app.services.state_store import get_state_store


def dispatch_oma_manuscript_message(
    message: AgentMessage,
    *,
    parent_state: AgentState,
    user_id: str,
) -> AgentMessage:
    """
    Execute reviewer/writer/editor capabilities bound to parent_task_id manuscript.
    context must include manuscript_paths and chapter_index.
    """
    capability = message.capability.lower().strip()
    ctx = message.payload.get("context") if isinstance(message.payload.get("context"), dict) else {}
    chapter_index = ctx.get("chapter_index")
    try:
        chapter_index = int(chapter_index) if chapter_index is not None else None
    except (TypeError, ValueError):
        chapter_index = None

    stored = get_state_store().load(parent_state["task_id"], read_only=True) or parent_state
    local = merge_state(
        stored,
        input_payload={
            **(stored.get("input_payload") or {}),
            "manuscript_paths": ctx.get("manuscript_paths"),
        },
    )

    from app.services.mission_oma.workers import (
        execute_oma_worker,
        run_chapter_review_worker,
    )

    if capability == "review_chapter" and chapter_index:
        outcome = run_chapter_review_worker(local, chapter_index=chapter_index)
        return AgentMessage(
            message_id=message.message_id,
            from_agent="reviewer",
            to_agent=message.from_agent,
            capability=capability,
            payload=outcome,
            correlation_id=message.correlation_id,
            message_type="result",
        )

    agent_map = {
        "review_chapter": "reviewer",
        "polish_chapter": "editor",
        "write_chapter": "writer",
        "append_body": "writer",
    }
    agent = agent_map.get(capability, "writer")
    wi_action = capability if capability != "write_chapter" else "append_body"
    local = merge_state(
        local,
        input_payload={
            **(local.get("input_payload") or {}),
            "writing_intent": {
                "enabled": True,
                "action": wi_action,
                "chapter_index": chapter_index,
                "source": "oma_a2a",
            },
            "oma_agent": agent,
            "oma_capability": capability,
        },
        step_decision={
            "action": "continue",
            "params": {
                "oma_agent": agent,
                "oma_capability": capability,
                "chapter_index": chapter_index,
            },
        },
    )
    result_state = execute_oma_worker(local)
    status = "COMPLETED" if not str(result_state.get("status", "")).endswith("FAILED") else "FAILED"
    return AgentMessage(
        message_id=message.message_id,
        from_agent=agent,
        to_agent=message.from_agent,
        capability=capability,
        payload={
            "status": status,
            "tool_results": result_state.get("tool_results"),
            "reasoning_result": result_state.get("reasoning_result"),
        },
        correlation_id=message.correlation_id,
        message_type="result" if status == "COMPLETED" else "error",
    )
