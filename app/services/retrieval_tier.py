"""Fast / slow retrieval tier decision (§7.3 scheme A)."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.retrieval_cache import cache_get


def fast_tier_enabled() -> bool:
    return bool(getattr(settings, "RETRIEVAL_FAST_TIER_ENABLED", True))


def should_try_fast_retrieval(
    state: AgentState | dict,
    query: str,
    domains: set[str],
) -> bool:
    """True when keyword-only / cache path is worth attempting before hybrid_search."""
    if not fast_tier_enabled():
        return False
    session_id = str(state.get("session_id") or state.get("task_id") or "")
    if cache_get(query, domains, session_id) is not None:
        return True
    payload = state.get("input_payload") or {}
    profile = str(payload.get("thin_execution_profile") or "")
    if profile in ("qa_direct", "session_source_qa"):
        return True
    from app.services.interaction_goal import goal_is_session_source_inquiry

    goal = str(payload.get("goal") or payload.get("query") or "").strip()
    if goal_is_session_source_inquiry(goal, state):
        return True
    return False
