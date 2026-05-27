"""Manuscript progress metrics — character counts vs raw bytes."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.services.manuscript_context import read_body_text


def count_prose_chars(text: str) -> int:
    """Count Chinese + word characters; ignore whitespace-only."""
    if not text:
        return 0
    stripped = re.sub(r"\s+", "", text)
    return len(stripped)


def body_written_chars(
    task_id: str,
    manuscript: dict[str, Any],
    *,
    state: Optional[dict[str, Any]] = None,
) -> int:
    """Prefer prose char count; fall back to body_bytes when file unreadable."""
    body_path = manuscript.get("body_path")
    if not body_path:
        return 0
    try:
        body_text = read_body_text(task_id, str(body_path), state=state)
        if body_text.strip():
            return count_prose_chars(body_text)
    except OSError:
        pass
    return int(manuscript.get("body_bytes") or 0)
