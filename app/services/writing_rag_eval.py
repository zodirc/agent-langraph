"""Writing-domain RAG impact metrics: format compliance and anti-AI voice."""

from __future__ import annotations

import re
from typing import Any

_CHAPTER_FOOTER_RE = re.compile(r"（第\s*\d+\s*章完）")
_AI_CLICHE_RE = re.compile(
    r"(众所周知|综上所述|不言而喻|值得一提的是|在这个.*?的世界里|仿佛时间都静止了)"
)
_META_COMMENTARY_RE = re.compile(r"(作为AI|我是一个语言模型|以下是我|根据您的要求)")


def score_format_compliance(text: str) -> float:
    """0-1 score for TXT layout cues (chapter footer, non-empty body)."""
    body = (text or "").strip()
    if len(body) < 80:
        return 0.0
    score = 0.5
    if _CHAPTER_FOOTER_RE.search(body):
        score += 0.35
    if "\n\n" in body:
        score += 0.15
    return min(1.0, score)


def score_anti_ai_voice(text: str) -> float:
    """0-1 score; higher means fewer AI clichés and meta commentary."""
    body = text or ""
    hits = len(_AI_CLICHE_RE.findall(body)) + len(_META_COMMENTARY_RE.findall(body))
    if hits == 0:
        return 1.0
    if hits == 1:
        return 0.7
    if hits == 2:
        return 0.45
    return 0.2


def score_writing_compliance(
    text: str,
    *,
    guidelines_excerpt: str = "",
) -> dict[str, Any]:
    """
    Aggregate writing compliance for A/B eval (with vs without RAG guidelines).

    When guidelines mention chapter footers, slightly weight format compliance.
    """
    fmt = score_format_compliance(text)
    anti_ai = score_anti_ai_voice(text)
    weights = (0.45, 0.55)
    if guidelines_excerpt and ("章" in guidelines_excerpt or "TXT" in guidelines_excerpt):
        weights = (0.55, 0.45)
    overall = weights[0] * fmt + weights[1] * anti_ai
    return {
        "format_compliance": round(fmt, 4),
        "anti_ai_score": round(anti_ai, 4),
        "overall": round(overall, 4),
    }


def compare_rag_ab_delta(
    with_rag: dict[str, float],
    without_rag: dict[str, float],
) -> dict[str, float]:
    """Return per-metric uplift when RAG guidelines are present."""
    keys = ("format_compliance", "anti_ai_score", "overall")
    return {
        f"{key}_delta": round(float(with_rag.get(key, 0.0)) - float(without_rag.get(key, 0.0)), 4)
        for key in keys
    }
