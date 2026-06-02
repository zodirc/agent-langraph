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


def should_skip_session_memory_retrieval(state: AgentState | dict) -> bool:
    """
    Autonomous writing missions do not use memory_hits for prose generation.

    Skip episodic recall on the first user turn of a writing mission; re-enable when
    the user sends another message in the same session (preferences / steer).
    """
    mission = state.get("mission") or (state.get("input_payload") or {}).get("mission") or {}
    if str(mission.get("kind") or "") != "writing":
        return False
    if int(state.get("session_turn") or 1) > 1:
        return False
    payload = state.get("input_payload") or {}
    intent = payload.get("writing_intent") or {}
    if not intent.get("enabled"):
        return False
    history = conversation_history_from_state(state)
    user_msgs = [m for m in history if str(m.get("role")) == "user"]
    return len(user_msgs) <= 1


def restrict_memory_to_current_session(state: AgentState | dict) -> bool:
    """
    First turn of a session window should not recall other sessions' episode memories.

    Memory search is user-scoped globally; without this, /new sessions still see old hits.
    """
    if int(state.get("session_turn") or 1) > 1:
        return False
    payload = state.get("input_payload") or {}
    goal = str(payload.get("goal") or payload.get("query") or payload.get("question") or "")
    from app.services.manuscript_service import is_continue_writing_goal

    if is_continue_writing_goal(goal):
        return False
    if payload.get("new_session") or payload.get("session_is_new"):
        return True
    history = payload.get("conversation_history") or state.get("conversation_history") or []
    user_msgs = [m for m in history if str(m.get("role")) == "user"]
    return len(user_msgs) <= 1
