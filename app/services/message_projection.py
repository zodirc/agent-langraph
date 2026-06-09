"""Project chat_message_events into structured_blocks read model."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# UI telemetry persisted in chat_message_events but excluded from structured_blocks.
TELEMETRY_EVENT_TYPES = frozenset(
    {
        "ui_task_created",
        "ui_progress",
        "ui_node",
        "ui_plan",
        "ui_ack",
    }
)

BLOCK_DISPLAY_ORDER: dict[str, int] = {
    "thinking": 0,
    "trace": 1,
    "writing": 2,
    "tool_preview": 3,
    "answer": 4,
    "answer_revoked": 5,
}


def is_telemetry_event(event_type: str) -> bool:
    return str(event_type or "") in TELEMETRY_EVENT_TYPES


def sort_blocks_for_display(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonical UI order: thinking → trace → writing → answer."""
    indexed = list(enumerate(blocks))
    indexed.sort(
        key=lambda pair: (
            BLOCK_DISPLAY_ORDER.get(str(pair[1].get("type") or ""), 99),
            pair[0],
        )
    )
    return [block for _, block in indexed]


def _find_block(blocks: list[dict[str, Any]], block_type: str, key: str, value: str) -> dict[str, Any] | None:
    for block in blocks:
        if block.get("type") == block_type and str(block.get(key) or "") == value:
            return block
    return None


def _last_block(blocks: list[dict[str, Any]], block_type: str) -> dict[str, Any] | None:
    for block in reversed(blocks):
        if block.get("type") == block_type:
            return block
    return None


def apply_event_to_blocks(
    blocks: list[dict[str, Any]],
    *,
    event_type: str,
    delta: str,
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return updated structured_blocks after applying one stream event."""
    out = deepcopy(blocks)
    et = str(event_type or "")

    if is_telemetry_event(et):
        return out

    if et == "thinking_delta":
        block = _last_block(out, "thinking")
        if block is None or block.get("status") == "completed":
            block = {"type": "thinking", "text": "", "status": "streaming"}
            out.append(block)
        block["text"] = str(block.get("text") or "") + str(delta or "")
        return out

    if et == "thinking_snapshot":
        text = str(delta or "")
        if not text.strip():
            return out
        block = _last_block(out, "thinking")
        if block is None:
            out.insert(0, {"type": "thinking", "text": text, "status": "completed"})
        else:
            existing = str(block.get("text") or "")
            if len(text) >= len(existing):
                block["text"] = text
            block["status"] = "completed"
        return out

    if et == "writing_delta":
        filename = str(meta.get("filename") or "").strip()
        block_key = filename or "__default__"
        block = _find_block(out, "writing", "block_key", block_key)
        reset = bool(meta.get("reset")) or str(meta.get("phase") or "") == "start"
        if block is None or reset:
            block = {
                "type": "writing",
                "block_key": block_key,
                "filename": filename,
                "text": "",
                "status": "streaming",
            }
            if reset and block in out:
                idx = out.index(block)
                out[idx] = block
            else:
                out.append(block)
        block["text"] = str(block.get("text") or "") + str(delta or "")
        block["filename"] = filename or block.get("filename") or ""
        return out

    if et in ("answer_delta", "answer_preview"):
        content_delta = str(delta or "")
        if content_delta:
            block = _last_block(out, "answer")
            if block is None:
                block = {"type": "answer", "text": "", "status": "streaming"}
                out.append(block)
            block["text"] = str(block.get("text") or "") + content_delta
        return out

    if et == "trace":
        out.append(
            {
                "type": "trace",
                "node": str(meta.get("node") or ""),
                "phase": str(meta.get("phase") or ""),
                "level": str(meta.get("level") or "delta"),
                "text": str(delta or meta.get("text") or ""),
            }
        )
        return out

    if et == "tool_preview":
        out.append(
            {
                "type": "tool_preview",
                "tool": str(meta.get("tool") or ""),
                "snippet": str(meta.get("snippet") or delta or ""),
                "status": str(meta.get("status") or ""),
            }
        )
        return out

    if et == "answer_revoked":
        out.append(
            {
                "type": "answer_revoked",
                "reason": str(meta.get("reason") or delta or ""),
            }
        )
        return out

    if delta or meta:
        out.append({"type": et or "event", "text": str(delta or ""), "meta": meta})
    return out


def finalize_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark streaming blocks as completed."""
    out = deepcopy(blocks)
    for block in out:
        if block.get("status") == "streaming":
            block["status"] = "completed"
    return out


def project_events_to_blocks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild structured_blocks by replaying persisted stream events."""
    blocks: list[dict[str, Any]] = []
    for event in events:
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        blocks = apply_event_to_blocks(
            blocks,
            event_type=str(event.get("event_type") or ""),
            delta=str(event.get("delta") or ""),
            meta=meta,
        )
    return sort_blocks_for_display(finalize_blocks(blocks))


def ensure_thinking_in_blocks(
    blocks: list[dict[str, Any]],
    thinking_text: str,
) -> list[dict[str, Any]]:
    """Ensure thinking block contains at least the given text (for in-flight draft recovery)."""
    text = str(thinking_text or "")
    if not text.strip():
        return blocks
    out = deepcopy(blocks)
    block = _last_block(out, "thinking")
    existing = str(block.get("text") or "") if block else ""
    if len(text) <= len(existing):
        return out
    if block is None:
        out.insert(0, {"type": "thinking", "text": text, "status": "streaming"})
    else:
        block["text"] = text
        if block.get("status") != "completed":
            block["status"] = "streaming"
    return sort_blocks_for_display(out)


def ensure_answer_in_blocks(
    blocks: list[dict[str, Any]],
    answer_text: str,
) -> list[dict[str, Any]]:
    """Ensure canonical final answer text is present in structured_blocks."""
    text = str(answer_text or "").strip()
    if not text:
        return blocks
    out = deepcopy(blocks)
    block = _last_block(out, "answer")
    existing = str(block.get("text") or "").strip() if block else ""
    if len(text) <= len(existing):
        return out
    if block is None:
        out.append({"type": "answer", "text": text, "status": "completed"})
    else:
        block["text"] = text
        if block.get("status") == "streaming":
            block["status"] = "completed"
    return out
