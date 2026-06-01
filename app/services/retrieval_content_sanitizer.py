"""
Sanitize retrieved content before it reaches reasoning/planning LLM context.

Mitigates prompt injection embedded in knowledge-base or memory hits.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.tool_intent_guard import INJECTION_MARKERS

_STRIP_PATTERNS = [
    re.compile(r"<\|im_start\|>.*?<\|im_end\|>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<\|system\|>.*?<\|end\|>", re.IGNORECASE | re.DOTALL),
    re.compile(r"```system\s*\n.*?\n```", re.IGNORECASE | re.DOTALL),
]


def _contains_injection(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker.lower() in lower for marker in INJECTION_MARKERS)


def sanitize_retrieved_text(text: str) -> str:
    """Strip injection markers and suspicious blocks from retrieved text."""
    cleaned = str(text or "")
    for pattern in _STRIP_PATTERNS:
        cleaned = pattern.sub("[sanitized]", cleaned)
    if _contains_injection(cleaned):
        for marker in INJECTION_MARKERS:
            cleaned = re.sub(re.escape(marker), "[filtered]", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def sanitize_retrieved_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a single retrieved knowledge document."""
    out = dict(doc)
    for key in ("content", "text", "snippet", "summary", "body"):
        if key in out and isinstance(out[key], str):
            out[key] = sanitize_retrieved_text(out[key])
    if _contains_injection(str(out.get("title") or "")):
        out["title"] = sanitize_retrieved_text(str(out["title"]))
    out["sanitized"] = True
    return out


def sanitize_retrieved_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [sanitize_retrieved_document(item) for item in (items or []) if isinstance(item, dict)]
