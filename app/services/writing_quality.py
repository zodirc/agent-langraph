"""Chapter quality rubric scoring and quality gate."""

from __future__ import annotations

import json
from typing import Any, Optional

from app.config.settings import settings
from app.domain.writing_memory_models import ChapterQualityRubric
from app.services.manuscript_context import is_near_duplicate_append


def score_chapter_quality_heuristic(
    *,
    chapter_text: str,
    prev_chapter_text: str = "",
    outline_slice: str = "",
    story_bible_excerpt: Optional[dict[str, Any]] = None,
) -> ChapterQualityRubric:
    """Deterministic rubric for tests and LLM-fallback."""
    text = (chapter_text or "").strip()
    prev = (prev_chapter_text or "").strip()
    outline = (outline_slice or "").strip()

    continuity = 0.75 if not prev else 0.55
    if prev and text:
        from difflib import SequenceMatcher

        head = text[:400]
        tail_prev = prev[-400:]
        continuity = min(1.0, SequenceMatcher(None, tail_prev, head).ratio() + 0.35)

    alignment = 0.5
    if outline and text:
        hits = sum(1 for token in _keywords(outline)[:12] if token in text)
        alignment = min(1.0, 0.4 + hits * 0.08)

    character = 0.7
    if story_bible_excerpt:
        names = list((story_bible_excerpt.get("characters") or {}).keys())[:6]
        if names:
            present = sum(1 for n in names if n in text)
            character = min(1.0, 0.45 + present / max(1, len(names)) * 0.5)

    dup_risk = 0.0
    if prev and text:
        dup, ratio = is_near_duplicate_append(prev, text, threshold=0.75)
        dup_risk = ratio if dup else max(0.0, ratio - 0.3)

    completion = min(1.0, len(text) / 800) if text else 0.0
    hook = 0.6
    if text and any(m in text[-300:] for m in ("？", "！", "……", "未完", "突然", "却")):
        hook = 0.75

    return ChapterQualityRubric(
        continuity_score=round(continuity, 3),
        outline_alignment=round(alignment, 3),
        character_consistency=round(character, 3),
        duplication_risk=round(dup_risk, 3),
        chapter_completion=round(completion, 3),
        hook_quality=round(hook, 3),
    )


def _keywords(text: str) -> list[str]:
    import re

    return [w for w in re.findall(r"[\u4e00-\u9fff]{2,}", text) if len(w) >= 2][:30]


def score_chapter_quality(
    *,
    chapter_text: str,
    prev_chapter_text: str = "",
    outline_slice: str = "",
    story_bible_excerpt: Optional[dict[str, Any]] = None,
    use_llm: bool = True,
) -> ChapterQualityRubric:
    heuristic = score_chapter_quality_heuristic(
        chapter_text=chapter_text,
        prev_chapter_text=prev_chapter_text,
        outline_slice=outline_slice,
        story_bible_excerpt=story_bible_excerpt,
    )
    if not use_llm or len((chapter_text or "").strip()) < 80:
        return heuristic
    try:
        from app.services.llm_client import invoke_structured

        system = (
            "Return JSON with floats 0-1: continuity_score, outline_alignment, "
            "character_consistency, duplication_risk (higher=worse), "
            "chapter_completion, hook_quality."
        )
        payload = {
            "chapter_text": (chapter_text or "")[:12000],
            "prev_chapter_text": (prev_chapter_text or "")[-4000:],
            "outline_for_chapter": (outline_slice or "")[:3000],
            "story_bible": story_bible_excerpt or {},
            "heuristic": heuristic.to_dict(),
        }
        result = invoke_structured(
            "reflection",
            system,
            json.dumps(payload, ensure_ascii=False),
        )
        return ChapterQualityRubric.from_dict({**heuristic.to_dict(), **result})
    except Exception:
        return heuristic


def quality_gate_threshold() -> float:
    return float(getattr(settings, "WRITING_QUALITY_GATE_THRESHOLD", 0.65))


def rubric_passes_gate(rubric: ChapterQualityRubric) -> bool:
    return rubric.pass_gate(threshold=quality_gate_threshold())
