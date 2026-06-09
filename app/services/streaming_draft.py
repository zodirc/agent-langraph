"""Persist in-flight assistant answer text for refresh recovery (Phase 1)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus, merge_state

STREAMING_STATUS_ACTIVE = "streaming"
STREAMING_STATUS_COMPLETED = "completed"

_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.REJECTED.value,
        TaskStatus.FAILED.value,
        TaskStatus.DEAD_LETTER.value,
        TaskStatus.ABANDONED.value,
        TaskStatus.CANCELLED.value,
    }
)


@dataclass
class AnswerDraftPersistCtx:
    """Accumulates answer_delta text during SSE and throttles StateStore saves."""

    state: AgentState
    accumulated: str = ""
    last_save_at: float = 0.0
    last_saved_len: int = 0


@dataclass
class ThinkingDraftPersistCtx:
    """Accumulates thinking_delta text during SSE and throttles StateStore saves."""

    state: AgentState
    accumulated: str = ""
    last_save_at: float = 0.0
    last_saved_len: int = 0


def streaming_draft_enabled() -> bool:
    return bool(getattr(settings, "STREAM_DRAFT_PERSIST_ENABLED", True))


def apply_streaming_draft(
    state: AgentState,
    text: str,
    *,
    status: str = STREAMING_STATUS_ACTIVE,
) -> AgentState:
    body = str(text or "")
    if not body.strip():
        return clear_streaming_draft(state)
    return merge_state(
        state,
        streaming_answer_text=body,
        streaming_answer_status=status,
    )


def clear_streaming_draft(state: AgentState) -> AgentState:
    cleared = state
    if state.get("streaming_answer_text") or state.get("streaming_answer_status"):
        cleared = merge_state(
            cleared,
            streaming_answer_text=None,
            streaming_answer_status=None,
        )
    return clear_streaming_thinking(cleared)


def apply_streaming_thinking(
    state: AgentState,
    text: str,
    *,
    status: str = STREAMING_STATUS_ACTIVE,
) -> AgentState:
    body = str(text or "")
    if not body.strip():
        return clear_streaming_thinking(state)
    return merge_state(
        state,
        streaming_thinking_text=body,
        streaming_thinking_status=status,
    )


def clear_streaming_thinking(state: AgentState) -> AgentState:
    if not state.get("streaming_thinking_text") and not state.get("streaming_thinking_status"):
        return state
    return merge_state(
        state,
        streaming_thinking_text=None,
        streaming_thinking_status=None,
    )


def resolve_streaming_thinking(state: AgentState) -> Optional[dict[str, Any]]:
    """Return active thinking draft for API/UI, or None when nothing to restore."""
    status = str(state.get("streaming_thinking_status") or "")
    text = str(state.get("streaming_thinking_text") or "").strip()
    if status != STREAMING_STATUS_ACTIVE or not text:
        return None
    task_status = str(state.get("status") or "")
    if task_status in _TERMINAL_STATUSES:
        return None
    return {
        "text": text,
        "active": True,
        "session_turn": int(state.get("session_turn") or 0),
        "status": status,
    }


def resolve_thinking_text_for_restore(state: AgentState) -> Optional[str]:
    """Return persisted thinking text for refresh recovery (any non-empty draft)."""
    text = str(state.get("streaming_thinking_text") or "").strip()
    return text or None


def maybe_persist_streaming_thinking(
    state: AgentState,
    accumulated: str,
    *,
    last_save_at: float,
    last_saved_len: int,
) -> tuple[AgentState, float, int, bool]:
    if not streaming_draft_enabled():
        return state, last_save_at, last_saved_len, False

    body = str(accumulated or "")
    if not body:
        return state, last_save_at, last_saved_len, False

    interval = float(getattr(settings, "STREAM_THINKING_SAVE_INTERVAL_SEC", 0.4))
    min_chars = int(getattr(settings, "STREAM_THINKING_SAVE_MIN_CHARS", 1))
    now = time.monotonic()
    delta_chars = len(body) - last_saved_len
    if last_save_at > 0 and (now - last_save_at) < interval and delta_chars < min_chars:
        return state, last_save_at, last_saved_len, False

    updated = apply_streaming_thinking(state, body)
    from app.services.live_task_state import touch_live_if_active
    from app.services.state_store import get_state_store

    updated = get_state_store().save(updated)
    touch_live_if_active(str(updated["task_id"]), updated)
    return updated, now, len(body), True


def absorb_thinking_delta(ctx: ThinkingDraftPersistCtx, delta: dict[str, Any]) -> None:
    text = str(delta.get("text") or "")
    if not text:
        return
    ctx.accumulated += text
    ctx.state, ctx = force_persist_thinking_draft(ctx, completed=False)


def force_persist_thinking_draft(
    ctx: ThinkingDraftPersistCtx,
    state: Optional[AgentState] = None,
    *,
    completed: bool = False,
) -> tuple[AgentState, ThinkingDraftPersistCtx]:
    """Flush full in-memory thinking buffer to StateStore (e.g. when reasoning ends)."""
    body = str(ctx.accumulated or "")
    if not body.strip():
        return state or ctx.state, ctx
    if not streaming_draft_enabled():
        return state or ctx.state, ctx
    status = STREAMING_STATUS_COMPLETED if completed else STREAMING_STATUS_ACTIVE
    base = state or ctx.state
    updated = apply_streaming_thinking(base, body, status=status)
    from app.services.live_task_state import touch_live_if_active
    from app.services.state_store import get_state_store

    updated = get_state_store().save(updated)
    touch_live_if_active(str(updated["task_id"]), updated)
    ctx.state = updated
    ctx.last_save_at = time.monotonic()
    ctx.last_saved_len = len(body)
    return updated, ctx


def resolve_streaming_draft(state: AgentState) -> Optional[dict[str, Any]]:
    """Return active draft metadata for API/UI, or None when nothing to restore."""
    status = str(state.get("streaming_answer_status") or "")
    text = str(state.get("streaming_answer_text") or "").strip()
    if status != STREAMING_STATUS_ACTIVE or not text:
        return None
    task_status = str(state.get("status") or "")
    if task_status in _TERMINAL_STATUSES:
        return None
    return {
        "text": text,
        "active": True,
        "session_turn": int(state.get("session_turn") or 0),
        "status": status,
    }


def maybe_persist_streaming_draft(
    state: AgentState,
    accumulated: str,
    *,
    last_save_at: float,
    last_saved_len: int,
) -> tuple[AgentState, float, int, bool]:
    """
    Throttled persist of accumulated answer text.

    Returns (updated_state, new_last_save_at, new_last_saved_len, did_save).
    """
    if not streaming_draft_enabled():
        return state, last_save_at, last_saved_len, False

    body = str(accumulated or "")
    if not body:
        return state, last_save_at, last_saved_len, False

    interval = float(getattr(settings, "STREAM_DRAFT_SAVE_INTERVAL_SEC", 2.0))
    min_chars = int(getattr(settings, "STREAM_DRAFT_SAVE_MIN_CHARS", 80))
    now = time.monotonic()
    delta_chars = len(body) - last_saved_len
    if last_save_at > 0 and (now - last_save_at) < interval and delta_chars < min_chars:
        return state, last_save_at, last_saved_len, False

    updated = apply_streaming_draft(state, body)
    from app.services.live_task_state import touch_live_if_active
    from app.services.state_store import get_state_store

    updated = get_state_store().save(updated)
    touch_live_if_active(str(updated["task_id"]), updated)
    return updated, now, len(body), True


def absorb_answer_delta(ctx: AnswerDraftPersistCtx, delta: dict[str, Any]) -> None:
    """Append one answer_delta payload and persist when thresholds are met."""
    text = str(delta.get("text") or "")
    if not text:
        return
    ctx.accumulated += text
    ctx.state, ctx.last_save_at, ctx.last_saved_len, _ = maybe_persist_streaming_draft(
        ctx.state,
        ctx.accumulated,
        last_save_at=ctx.last_save_at,
        last_saved_len=ctx.last_saved_len,
    )


def finalize_streaming_draft(state: AgentState, *, completed: bool = True) -> AgentState:
    """Mark draft completed or clear after turn finalization."""
    if completed and str(state.get("streaming_answer_text") or "").strip():
        return apply_streaming_draft(
            state,
            str(state.get("streaming_answer_text") or ""),
            status=STREAMING_STATUS_COMPLETED,
        )
    return clear_streaming_draft(state)
