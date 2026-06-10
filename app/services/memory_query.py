"""Build retrieval queries for session-scoped memory search."""

from __future__ import annotations

from app.runtime.state import AgentState
from app.services.conversation_context import (
    conversation_history_for_llm,
    conversation_history_from_state,
)


def should_suppress_session_memory(state: AgentState | dict) -> bool:
    payload = state.get("input_payload") or {}
    audit = payload.get("route_audit") or {}
    task_type = str(state.get("task_type") or payload.get("task_type") or "qa").lower()
    inferred_kind = str(audit.get("inferred_kind") or "").lower()
    profile = str(payload.get("artifact_profile") or audit.get("artifact_profile") or "").lower()
    turn = int(state.get("session_turn") or 0)
    return (
        turn <= 2
        and task_type == "qa"
        and inferred_kind in {"", "code", "qa"}
        and profile in {"", "source_code"}
    )


def build_memory_search_query(state: AgentState | dict) -> str:
    """Combine current goal with the latest user turn for better episodic recall."""
    payload = state.get("input_payload") or {}
    parts: list[str] = []
    goal = str(payload.get("goal") or payload.get("query") or payload.get("question") or "").strip()
    if goal:
        parts.append(goal)

    if should_suppress_session_memory(state):
        return " ".join(parts)[:2000].strip() or str(state.get("task_type") or "qa")

    history = conversation_history_for_llm(conversation_history_from_state(state))
    for msg in reversed(history):
        if str(msg.get("role")) == "user":
            prev = str(msg.get("content") or "").strip()
            if prev and prev != goal:
                parts.append(prev)
            break

    turn = int(state.get("session_turn") or 0)
    if turn > 1:
        parts.append(f"session turn {turn}")

    return " ".join(parts)[:2000].strip() or str(state.get("task_type") or "qa")
