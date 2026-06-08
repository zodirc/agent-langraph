"""Mission inline pipeline routing (not on frozen main graph spine)."""

from __future__ import annotations

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus


def route_after_writing(state: AgentState) -> str:
    """Mission inline pipeline only — not on frozen main graph spine."""
    status = str(state.get("status", ""))
    if status == TaskStatus.WRITING_FAILED.value:
        if state.get("retry_count", 0) >= settings.MAX_RETRY_COUNT:
            return "dead_letter"
        return "writing"
    return "reasoning"
