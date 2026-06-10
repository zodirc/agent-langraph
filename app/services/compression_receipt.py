"""Structured compression audit receipts (P0-2)."""

from __future__ import annotations

import re
from typing import Any

_ENTITY_RE = re.compile(r"[\w\u4e00-\u9fff]{3,}")
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")


def tokenize_for_scoring(text: str) -> set[str]:
    return {t.lower() for t in _ENTITY_RE.findall(text or "") if len(t) > 1}


def build_compression_receipt(
    before: str,
    after: str,
    *,
    anchors: list[str] | None = None,
) -> dict[str, Any]:
    """Return dropped entity tokens and anchors still present after compression."""
    before_entities = set(_ENTITY_RE.findall(before or ""))
    after_entities = set(_ENTITY_RE.findall(after or ""))
    dropped = sorted(before_entities - after_entities)[:24]
    kept_anchors: list[str] = []
    for anchor in anchors or []:
        if anchor and anchor in (after or ""):
            kept_anchors.append(anchor)
    return {"dropped_entities": dropped, "kept_anchors": kept_anchors}


def extractive_sentences(
    text: str,
    *,
    query: str = "",
    max_chars: int,
    max_sentences: int = 8,
) -> tuple[str, dict[str, Any]]:
    """Score sentences by query-term overlap; preserve original order in output."""
    if not text:
        return "", build_compression_receipt("", "", anchors=tokenize_for_scoring(query))

    query_terms = tokenize_for_scoring(query)
    anchors = list(query_terms)[:12]
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if s.strip()]
    if not sentences:
        clipped = (text or "")[:max_chars]
        if len(text or "") > len(clipped):
            clipped += "…"
        return clipped, build_compression_receipt(text, clipped, anchors=anchors)

    scored: list[tuple[int, float, str]] = []
    for idx, sent in enumerate(sentences):
        sent_terms = tokenize_for_scoring(sent)
        overlap = len(sent_terms & query_terms) if query_terms else 0.0
        score = overlap / max(len(query_terms), 1) if query_terms else 0.0
        if sent.lower().startswith(("conclusion", "summary", "result", "finding")):
            score += 0.5
        scored.append((idx, score, sent))

    ranked = sorted(scored, key=lambda x: x[1], reverse=True)
    picked: list[int] = []
    total = 0
    for idx, _score, sent in ranked:
        if len(picked) >= max_sentences:
            break
        add_len = len(sent) + (1 if picked else 0)
        if total + add_len > max_chars and picked:
            continue
        picked.append(idx)
        total += add_len

    if not picked:
        first = sentences[0][:max_chars]
        if len(sentences[0]) > len(first):
            first += "…"
        return first, build_compression_receipt(text, first, anchors=anchors)

    ordered = [sentences[i] for i in sorted(picked)]
    clipped = "\n".join(ordered)
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars] + "…"
    return clipped, build_compression_receipt(text, clipped, anchors=anchors)
