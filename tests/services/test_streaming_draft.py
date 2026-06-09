"""Tests for streaming answer draft persistence (Phase 1 refresh recovery)."""

from __future__ import annotations

from unittest.mock import patch

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.streaming_draft import (
    AnswerDraftPersistCtx,
    absorb_answer_delta,
    apply_streaming_draft,
    clear_streaming_draft,
    maybe_persist_streaming_draft,
    resolve_streaming_draft,
    streaming_draft_enabled,
)


def test_apply_and_resolve_streaming_draft():
    state = create_initial_state()
    state = merge_state(state, session_turn=2, status=TaskStatus.REASONED.value)
    state = apply_streaming_draft(state, "Hello world")
    draft = resolve_streaming_draft(state)
    assert draft is not None
    assert draft["text"] == "Hello world"
    assert draft["active"] is True
    assert draft["session_turn"] == 2


def test_resolve_streaming_draft_hidden_when_terminal():
    state = create_initial_state()
    state = apply_streaming_draft(state, "partial")
    state = merge_state(state, status=TaskStatus.COMPLETED.value)
    assert resolve_streaming_draft(state) is None


def test_clear_streaming_draft():
    state = apply_streaming_draft(create_initial_state(), "x")
    cleared = clear_streaming_draft(state)
    assert cleared.get("streaming_answer_text") is None
    assert cleared.get("streaming_answer_status") is None


def test_absorb_answer_delta_accumulates():
    state = create_initial_state()
    ctx = AnswerDraftPersistCtx(state=state)
    with patch("app.services.state_store.get_state_store") as mock_store:
        mock_store.return_value.save.side_effect = lambda s: s
        with patch("app.services.live_task_state.touch_live_if_active"):
            absorb_answer_delta(ctx, {"text": "Hel"})
            absorb_answer_delta(ctx, {"text": "lo"})
    assert ctx.accumulated == "Hello"
    assert ctx.state.get("streaming_answer_text") in ("Hel", "Hello")


def test_maybe_persist_throttles_by_interval():
    state = create_initial_state()
    with patch("app.services.state_store.get_state_store") as mock_store:
        mock_store.return_value.save.side_effect = lambda s: s
        with patch("app.services.live_task_state.touch_live_if_active"):
            _, t1, len1, saved1 = maybe_persist_streaming_draft(
                state, "short", last_save_at=100.0, last_saved_len=0
            )
            _, _, _, saved2 = maybe_persist_streaming_draft(
                state, "short text", last_save_at=t1, last_saved_len=len1
            )
    assert saved1 is True
    assert saved2 is False


def test_streaming_draft_enabled_respects_settings():
    with patch("app.services.streaming_draft.settings") as mock_settings:
        mock_settings.STREAM_DRAFT_PERSIST_ENABLED = False
        assert streaming_draft_enabled() is False
