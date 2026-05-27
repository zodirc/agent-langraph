"""LLM-based semantic compression for long memory episodes (§5.5)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

MEMORY_COMPRESS_SYSTEM = (
    "Compress the episode into a dense summary under 800 characters. "
    "Preserve: goal, key decisions, tool outcomes, final result, errors. "
    'Return JSON: {"compressed": "<summary>"}'
)


def compress_episode_summary(
    full_summary: str,
    *,
    payload: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """
    Return (summary, was_compressed).
    Keeps original in payload['raw_summary'] when compressed.
    """
    threshold = settings.MEMORY_COMPRESS_MIN_CHARS
    if len(full_summary) < threshold or not settings.MEMORY_COMPRESS_ENABLED or not settings.MODEL_ENABLED:
        return full_summary, False
    try:
        from app.services.llm_client import invoke_structured

        user = json.dumps(
            {"episode": full_summary[:12000], "extra": payload or {}},
            ensure_ascii=False,
        )
        result = invoke_structured("summarization", MEMORY_COMPRESS_SYSTEM, user)
        compressed = str(result.get("compressed", "")).strip()
        if compressed and len(compressed) < len(full_summary):
            return compressed[:2000], True
    except Exception as exc:
        logger.warning("Memory compress failed, using truncate: %s", exc)
    return full_summary[:2000], False
