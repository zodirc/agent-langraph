"""Chat message orchestration: write path + SessionViewModel read path."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus
from app.services.chat_message_store import (
    MESSAGE_STATUS_COMPLETED,
    MESSAGE_STATUS_FAILED,
    MESSAGE_STATUS_INTERRUPTED,
    MESSAGE_STATUS_STREAMING,
    get_chat_message_store,
)
from app.services.message_projection import (
    apply_event_to_blocks,
    ensure_answer_in_blocks,
    ensure_thinking_in_blocks,
    finalize_blocks,
    is_telemetry_event,
    project_events_to_blocks,
    sort_blocks_for_display,
)


def chat_message_persist_enabled() -> bool:
    return bool(getattr(settings, "CHAT_MESSAGE_PERSIST_ENABLED", True))


@dataclass
class StreamMessagePersistCtx:
    task_id: str
    session_id: str
    session_turn: int
    user_message_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    next_seq: int = 1
    structured_blocks: list[dict[str, Any]] = field(default_factory=list)
    answer_text: str = ""
    last_persist_at: float = 0.0
    last_persist_seq: int = 0


class ChatMessageService:
    def __init__(self) -> None:
        self._store = get_chat_message_store()

    def record_user_message(
        self,
        state: AgentState,
        *,
        text: str,
        client_message_id: Optional[str] = None,
    ) -> Optional[str]:
        if not chat_message_persist_enabled():
            return None
        body = str(text or "").strip()
        if not body:
            return None
        task_id = str(state["task_id"])
        session_id = str(state.get("session_id") or task_id)
        turn = int(state.get("session_turn") or 0)
        if client_message_id:
            for msg in self._store.list_messages(task_id, limit=50):
                if (
                    msg.get("role") == "user"
                    and msg.get("session_turn") == turn
                    and msg.get("client_message_id") == client_message_id
                ):
                    return str(msg["message_id"])
        row = self._store.create_message(
            task_id=task_id,
            session_id=session_id,
            session_turn=turn,
            role="user",
            status=MESSAGE_STATUS_COMPLETED,
            content=body,
            client_message_id=client_message_id,
        )
        return str(row["message_id"])

    def begin_assistant_message(self, ctx: StreamMessagePersistCtx) -> str:
        if ctx.assistant_message_id:
            return ctx.assistant_message_id
        row = self._store.create_message(
            task_id=ctx.task_id,
            session_id=ctx.session_id,
            session_turn=ctx.session_turn,
            role="assistant",
            status=MESSAGE_STATUS_STREAMING,
        )
        ctx.assistant_message_id = str(row["message_id"])
        ctx.structured_blocks = []
        ctx.answer_text = ""
        return ctx.assistant_message_id

    def record_stream_event(
        self,
        ctx: StreamMessagePersistCtx,
        *,
        event_type: str,
        delta: str = "",
        meta: Optional[dict[str, Any]] = None,
    ) -> int:
        if not chat_message_persist_enabled():
            return 0
        self.begin_assistant_message(ctx)
        assert ctx.assistant_message_id
        meta = dict(meta or {})
        seq = ctx.next_seq
        ctx.next_seq += 1
        self._store.append_event(
            message_id=ctx.assistant_message_id,
            task_id=ctx.task_id,
            seq=seq,
            event_type=event_type,
            delta=delta,
            meta=meta,
        )
        if not is_telemetry_event(event_type):
            ctx.structured_blocks = apply_event_to_blocks(
                ctx.structured_blocks,
                event_type=event_type,
                delta=delta,
                meta=meta,
            )
        if event_type in ("answer_delta", "answer_preview"):
            ctx.answer_text += str(delta or "")
        if event_type in ("thinking_delta", "thinking_snapshot"):
            self._force_flush_message(ctx)
        else:
            self._maybe_flush_message(ctx)
        return seq

    def _force_flush_message(self, ctx: StreamMessagePersistCtx) -> None:
        assert ctx.assistant_message_id
        self._store.update_message(
            ctx.assistant_message_id,
            content=ctx.answer_text,
            structured_blocks=ctx.structured_blocks,
        )
        ctx.last_persist_at = time.monotonic()
        ctx.last_persist_seq = ctx.next_seq - 1

    def _maybe_flush_message(self, ctx: StreamMessagePersistCtx) -> None:
        assert ctx.assistant_message_id
        interval = float(getattr(settings, "CHAT_MESSAGE_FLUSH_INTERVAL_SEC", 2.0))
        min_events = int(getattr(settings, "CHAT_MESSAGE_FLUSH_MIN_EVENTS", 4))
        now = time.monotonic()
        events_since = ctx.next_seq - 1 - ctx.last_persist_seq
        if (
            ctx.last_persist_at > 0
            and (now - ctx.last_persist_at) < interval
            and events_since < min_events
        ):
            return
        self._store.update_message(
            ctx.assistant_message_id,
            content=ctx.answer_text,
            structured_blocks=ctx.structured_blocks,
        )
        ctx.last_persist_at = now
        ctx.last_persist_seq = ctx.next_seq - 1

    def finalize_assistant_message(
        self,
        ctx: StreamMessagePersistCtx,
        *,
        status: str = MESSAGE_STATUS_COMPLETED,
        content: Optional[str] = None,
        thinking_text: Optional[str] = None,
    ) -> None:
        if not chat_message_persist_enabled() or not ctx.assistant_message_id:
            return
        final_content = content if content is not None else ctx.answer_text
        mid = str(ctx.assistant_message_id)
        msg_events: list[dict[str, Any]] = []
        after_seq = 0
        while True:
            batch = self._store.list_events(ctx.task_id, after_seq=after_seq, limit=5000)
            if not batch:
                break
            msg_events.extend(e for e in batch if str(e.get("message_id") or "") == mid)
            if len(batch) < 5000:
                break
            after_seq = int(batch[-1]["seq"])
        if msg_events:
            blocks = project_events_to_blocks(msg_events)
        else:
            blocks = finalize_blocks(ctx.structured_blocks)
        blocks = sort_blocks_for_display(finalize_blocks(blocks))
        blocks = ensure_answer_in_blocks(blocks, final_content)
        if thinking_text:
            blocks = ensure_thinking_in_blocks(blocks, thinking_text)
        self._store.update_message(
            ctx.assistant_message_id,
            status=status,
            content=final_content,
            structured_blocks=blocks,
        )

    def sync_thinking_text(
        self,
        ctx: StreamMessagePersistCtx,
        full_text: str,
        *,
        persist_snapshot_event: bool = False,
    ) -> None:
        """Ensure assistant message thinking block contains the canonical full text."""
        if not chat_message_persist_enabled() or not ctx.assistant_message_id:
            return
        text = str(full_text or "").strip()
        if not text:
            return
        self.begin_assistant_message(ctx)
        if persist_snapshot_event:
            self.record_stream_event(
                ctx,
                event_type="thinking_snapshot",
                delta=text,
                meta={"snapshot": True},
            )
            return
        blocks = ensure_thinking_in_blocks(ctx.structured_blocks or [], text)
        ctx.structured_blocks = blocks
        self._store.update_message(
            ctx.assistant_message_id,
            structured_blocks=blocks,
        )

    def build_session_view(
        self,
        task_id: str,
        *,
        task_state: Optional[AgentState] = None,
    ) -> dict[str, Any]:
        if task_state is None:
            from app.services.state_store import get_state_store

            task_state = get_state_store().load(task_id, read_only=True)

        messages = self._store.list_messages(task_id)
        all_events: list[dict[str, Any]] = []
        after_seq = 0
        while True:
            batch = self._store.list_events(task_id, after_seq=after_seq, limit=5000)
            if not batch:
                break
            all_events.extend(batch)
            if len(batch) < 5000:
                break
            after_seq = int(batch[-1]["seq"])
        events_by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in all_events:
            events_by_message[str(event.get("message_id") or "")].append(event)

        task_status = str(task_state.get("status") or "") if task_state else ""
        task_terminal = task_status in {
            TaskStatus.COMPLETED.value,
            TaskStatus.REJECTED.value,
            TaskStatus.FAILED.value,
            TaskStatus.DEAD_LETTER.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.ABANDONED.value,
            TaskStatus.WAITING_REVIEW.value,
            TaskStatus.REVIEW_RESOLVED.value,
        }
        final_answer: Optional[str] = None
        if task_state:
            from app.services.confirmation.stream_display import client_final_answer

            final_answer = client_final_answer(task_state)

        last_seq = int(all_events[-1]["seq"]) if all_events else 0
        from app.services.streaming_draft import resolve_thinking_text_for_restore

        thinking_from_state = (
            resolve_thinking_text_for_restore(task_state) if task_state else None
        ) or ""
        streaming_assistant_id: Optional[str] = None
        if not task_terminal:
            for msg in reversed(messages):
                if (
                    msg.get("role") == "assistant"
                    and msg.get("status") == MESSAGE_STATUS_STREAMING
                ):
                    streaming_assistant_id = str(msg.get("message_id") or "") or None
                    break
        last_assistant_id: Optional[str] = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                last_assistant_id = str(msg.get("message_id") or "") or None
                break
        enriched: list[dict[str, Any]] = []
        for msg in messages:
            row = dict(msg)
            if row.get("role") == "assistant":
                msg_id = str(row.get("message_id") or "")
                evs = events_by_message.get(msg_id, [])
                content = str(row.get("content") or "").strip()
                if task_terminal and final_answer and msg_id == last_assistant_id:
                    content = final_answer
                if evs:
                    blocks = ensure_answer_in_blocks(
                        project_events_to_blocks(evs),
                        content,
                    )
                    row["structured_blocks"] = sort_blocks_for_display(blocks)
                elif task_terminal and final_answer and msg_id == last_assistant_id:
                    row["structured_blocks"] = sort_blocks_for_display(
                        ensure_answer_in_blocks([], final_answer)
                    )
                if (
                    thinking_from_state
                    and streaming_assistant_id
                    and msg_id == streaming_assistant_id
                ):
                    blocks = row.get("structured_blocks") or []
                    row["structured_blocks"] = ensure_thinking_in_blocks(
                        blocks if isinstance(blocks, list) else [],
                        thinking_from_state,
                    )
                    row["streaming_thinking_text"] = thinking_from_state
                if content:
                    row["content"] = content
                if task_terminal and row.get("status") == MESSAGE_STATUS_STREAMING:
                    row["status"] = (
                        self.status_for_turn_end(task_state)
                        if task_state
                        else MESSAGE_STATUS_COMPLETED
                    )
            enriched.append(row)

        streaming = None
        if not task_terminal:
            streaming = next(
                (
                    m
                    for m in reversed(enriched)
                    if m.get("role") == "assistant" and m.get("status") == MESSAGE_STATUS_STREAMING
                ),
                None,
            )
        return {
            "task_id": task_id,
            "session_id": enriched[0]["session_id"] if enriched else task_id,
            "messages": enriched,
            "message_count": len(enriched),
            "last_event_seq": last_seq,
            "streaming_message_id": streaming.get("message_id") if streaming else None,
            "streaming_answer_active": streaming is not None,
            "task_status": task_status,
            "final_answer": final_answer,
            "task_terminal": task_terminal,
        }

    def status_for_turn_end(self, state: AgentState) -> str:
        status = str(state.get("status") or "")
        if status in (
            TaskStatus.FAILED.value,
            TaskStatus.DEAD_LETTER.value,
            TaskStatus.REJECTED.value,
        ):
            return MESSAGE_STATUS_FAILED
        if status in (TaskStatus.WAITING_REVIEW.value, TaskStatus.PAUSED.value):
            return MESSAGE_STATUS_INTERRUPTED
        return MESSAGE_STATUS_COMPLETED


_service: ChatMessageService | None = None


def get_chat_message_service() -> ChatMessageService:
    global _service
    if _service is None:
        _service = ChatMessageService()
    return _service


def user_text_from_state(state: AgentState) -> str:
    payload = state.get("input_payload") or {}
    return str(
        payload.get("goal") or payload.get("query") or payload.get("question") or ""
    ).strip()
