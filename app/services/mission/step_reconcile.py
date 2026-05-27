"""Reconcile writing_intent chapter_index with manuscript cursor."""

from __future__ import annotations

from typing import Any

from app.services.manuscript_context import parse_last_chapter_index, read_body_text
from app.services.manuscript_service import manuscript_has_body, resolve_manuscript


def reconcile_writing_intent(state: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    """
    Align chapter_index with authoritative manuscript state.
    Manuscript wins on mismatch; records reconcile note in intent.
    """
    if not intent.get("enabled"):
        return intent

    action = str(intent.get("action") or "")
    if action not in ("append_body", "write_body", "append_chapter"):
        return intent

    task_id = str(state["task_id"])
    ms = resolve_manuscript(task_id, state.get("manuscript"))
    if not manuscript_has_body(ms):
        out = dict(intent)
        out["chapter_index"] = 1
        return out

    body_path = ms.body_path
    if not body_path:
        return intent

    body_text = read_body_text(task_id, body_path, state=state)
    last_ch = parse_last_chapter_index(body_text)
    cursor = int(ms.last_chapter_index or ms.chapter_cursor or 0)
    authoritative = max(last_ch, cursor)
    expected_next = max(1, authoritative + 1)

    current = intent.get("chapter_index")
    out = dict(intent)
    if current is None:
        out["chapter_index"] = expected_next
    elif int(current) != expected_next:
        out["chapter_index"] = expected_next
        out["chapter_reconciled_from"] = int(current)
    return out
