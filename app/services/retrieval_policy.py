"""When to run retrieval / session memory for multi-turn QA."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.conversation_context import conversation_history_from_state


def needs_session_memory_retrieval(state: AgentState | dict) -> bool:
    """Multi-turn sessions should recall prior turns even when knowledge RAG is skipped."""
    if not getattr(settings, "SESSION_MEMORY_RETRIEVAL_ENABLED", True):
        return False
    if not settings.SESSION_ENABLED:
        return False
    if int(state.get("session_turn") or 1) > 1:
        return True
    history = conversation_history_from_state(state)
    user_msgs = sum(1 for m in history if str(m.get("role")) == "user")
    return user_msgs >= 1 and len(history) >= 2


def should_route_to_retrieval_after_planning(state: AgentState) -> bool:
    """True when retrieval node should run (knowledge and/or session memory)."""
    if not state.get("skip_retrieval"):
        return True
    return needs_session_memory_retrieval(state)


def skip_knowledge_retrieval(state: AgentState) -> bool:
    """Planning may skip hybrid knowledge search while still allowing session memory."""
    return bool(state.get("skip_retrieval"))
