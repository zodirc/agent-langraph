"""Replay persisted chat_message_events as SSE (Phase 3 resume)."""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

from app.services.chat_message_store import get_chat_message_store
from app.services.graph_runner import _format_stream_event
from app.services.live_task_state import get_live


def _event_to_sse(task_id: str, event: dict[str, Any]) -> str:
    et = str(event.get("event_type") or "")
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    delta = str(event.get("delta") or "")
    seq = event.get("seq")

    if et == "ui_task_created":
        payload = {**meta, "task_id": task_id, "seq": seq, "resume": True}
        return _format_stream_event("task_created", payload)

    if et == "ui_progress":
        payload = {
            "task_id": task_id,
            "message": delta,
            "elapsed_sec": meta.get("elapsed_sec", 0),
            "phase": meta.get("phase", "working"),
            "seq": seq,
            "resume": True,
        }
        return _format_stream_event("progress", payload)

    if et == "ui_node":
        payload = {**meta, "task_id": task_id, "seq": seq, "resume": True}
        return _format_stream_event("node", payload)

    if et == "ui_plan":
        payload = {**meta, "task_id": task_id, "seq": seq, "resume": True}
        return _format_stream_event("plan", payload)

    if et == "ui_ack":
        payload = {**meta, "task_id": task_id, "seq": seq, "resume": True}
        return _format_stream_event("ack", payload)

    if et == "thinking_delta":
        payload = {
            "task_id": task_id,
            "node": meta.get("node"),
            "phase": meta.get("phase"),
            "text": delta,
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("thinking_delta", payload)

    if et == "thinking_snapshot":
        payload = {
            "task_id": task_id,
            "node": meta.get("node"),
            "phase": meta.get("phase"),
            "text": delta,
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("thinking_snapshot", payload)

    if et == "writing_delta":
        payload = {
            "task_id": task_id,
            "node": meta.get("node"),
            "phase": meta.get("phase"),
            "filename": meta.get("filename", ""),
            "text": delta,
            "reset": bool(meta.get("reset")),
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("writing_delta", payload)

    if et in ("answer_delta", "answer_preview"):
        payload = {
            "task_id": task_id,
            "node": meta.get("node"),
            "phase": meta.get("phase"),
            "field": meta.get("field", "summary"),
            "text": delta,
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("answer_delta" if et == "answer_delta" else "answer_preview", payload)

    if et == "trace":
        payload = {
            "task_id": task_id,
            "node": meta.get("node"),
            "phase": meta.get("phase"),
            "field": meta.get("field"),
            "text": delta,
            "level": meta.get("level", "delta"),
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("trace", payload)

    if et == "tool_preview":
        payload = {
            "task_id": task_id,
            "tool": meta.get("tool"),
            "status": meta.get("status"),
            "snippet": delta or meta.get("snippet"),
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("tool_preview", payload)

    if et == "answer_revoked":
        payload = {
            "task_id": task_id,
            "reason": delta or meta.get("reason"),
            "seq": event.get("seq"),
            "resume": True,
        }
        return _format_stream_event("answer_revoked", payload)

    payload = {
        "task_id": task_id,
        "event_type": et,
        "text": delta,
        "meta": meta,
        "seq": event.get("seq"),
        "resume": True,
    }
    return _format_stream_event("message_event", payload)


def iter_resumed_sse(
    task_id: str,
    *,
    after_seq: int = 0,
    tail_live: bool = False,
    poll_interval_sec: float = 1.0,
    max_tail_seconds: float = 300.0,
) -> Iterator[str]:
    """
    Yield SSE chunks from chat_message_events.

    When tail_live=True, poll for new events while the task is still running.
    """
    store = get_chat_message_store()
    cursor = int(after_seq)
    started = time.monotonic()

    yield _format_stream_event(
        "stream_resume",
        {"task_id": task_id, "after_seq": cursor, "tail_live": tail_live},
    )

    while True:
        events = store.list_events(task_id, after_seq=cursor, limit=500)
        for event in events:
            cursor = int(event["seq"])
            yield _event_to_sse(task_id, event)

        if not tail_live:
            break

        from app.services.graph_run_registry import is_graph_run_active

        live = get_live(task_id)
        graph_active = is_graph_run_active(task_id)
        if not graph_active and (live is None or not live.running):
            break
        if (time.monotonic() - started) >= max_tail_seconds:
            break
        time.sleep(max(0.2, float(poll_interval_sec)))

    view = get_chat_message_store().list_events(task_id, after_seq=0, limit=1)
    last_seq = cursor
    if view:
        rows = store.list_events(task_id, after_seq=0, limit=5000)
        if rows:
            last_seq = int(rows[-1]["seq"])

    yield _format_stream_event(
        "stream_resume_done",
        {"task_id": task_id, "last_event_seq": last_seq, "tail_live": tail_live},
    )


def list_events_json(task_id: str, *, after_seq: int = 0, limit: int = 2000) -> dict[str, Any]:
    events = get_chat_message_store().list_events(task_id, after_seq=after_seq, limit=limit)
    last_seq = int(events[-1]["seq"]) if events else int(after_seq)
    return {
        "task_id": task_id,
        "after_seq": int(after_seq),
        "events": events,
        "last_event_seq": last_seq,
        "has_more": len(events) >= int(limit),
    }
