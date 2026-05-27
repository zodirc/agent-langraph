from __future__ import annotations

import re
from typing import Any


DANGEROUS_PATTERNS = [
    r"ignore (previous|all) instructions",
    r"forget (your|the) (instructions|context)",
    r"you are now",
    r"<\|system\|>",
    r"disregard (all|previous)",
]


def sanitize_text(value: str) -> str:
    """Reject prompt-injection style instructions in user-provided text."""
    text = value.strip()
    if not text:
        raise ValueError("Input text cannot be empty")
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            raise ValueError("Detected potential prompt injection attempt")
    return text


def sanitize_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize common user-input fields inside task payloads."""
    cleaned = dict(payload)
    for key in ("goal", "query", "question", "message", "prompt"):
        if key in cleaned and isinstance(cleaned[key], str):
            cleaned[key] = sanitize_text(cleaned[key])
    return cleaned
