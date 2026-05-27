"""Split user-facing text into prose vs code regions for policy/guard pipelines."""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\s*([\s\S]*?)```", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


@dataclass(frozen=True)
class ContentSegment:
    kind: str  # prose | fenced_code | inline_code
    text: str
    language: str = ""


def split_content_segments(text: str) -> list[ContentSegment]:
    """Partition markdown-ish answer text; code regions are excluded from prose PII scans."""
    if not text.strip():
        return []

    segments: list[ContentSegment] = []
    cursor = 0
    for match in _FENCE_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            segments.extend(_split_prose_with_inline(text[cursor:start]))
        lang = str(match.group(1) or "").strip().lower()
        body = match.group(2) or ""
        segments.append(ContentSegment(kind="fenced_code", text=body, language=lang))
        cursor = end
    if cursor < len(text):
        segments.extend(_split_prose_with_inline(text[cursor:]))
    return segments


def _split_prose_with_inline(chunk: str) -> list[ContentSegment]:
    if not chunk.strip():
        return []
    out: list[ContentSegment] = []
    pos = 0
    for match in _INLINE_CODE_RE.finditer(chunk):
        s, e = match.span()
        if s > pos:
            prose = chunk[pos:s]
            if prose.strip():
                out.append(ContentSegment(kind="prose", text=prose))
        out.append(ContentSegment(kind="inline_code", text=match.group(1) or ""))
        pos = e
    if pos < len(chunk):
        tail = chunk[pos:]
        if tail.strip():
            out.append(ContentSegment(kind="prose", text=tail))
    if not out and chunk.strip():
        out.append(ContentSegment(kind="prose", text=chunk))
    return out


def prose_text(segments: list[ContentSegment]) -> str:
    return "".join(s.text for s in segments if s.kind == "prose").strip()
