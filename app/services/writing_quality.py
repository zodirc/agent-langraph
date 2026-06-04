"""Chapter quality rubric scoring and quality gate."""

from __future__ import annotations

import json
from typing import Any, Optional

from app.config.settings import settings
from app.domain.writing_memory_models import ChapterQualityRubric
from app.services.manuscript_context import is_near_duplicate_append

_QUALITY_LLM_SYSTEM = """You are a fiction editor scoring ONE chapter (Chinese web novel).

Return JSON only with floats 0.0-1.0:
- continuity_score: narrative continuity from previous chapter (time/place/characters/causality), NOT literal text overlap
- outline_alignment: how well this chapter fulfills outline_for_chapter plot beats
- character_consistency: character voices and facts vs story_bible
- duplication_risk: 0=original, 1=heavy repeat of previous chapter
- chapter_completion: structural completeness of this chapter
- hook_quality: strength of chapter-end hook / tension for next chapter
- notes: <=120 Chinese chars explaining main weaknesses (optional string)

Use heuristic scores as hints only; override when narrative judgment differs."""


def quality_score_use_llm() -> bool:
    """Whether chapter rubric should be judged by LLM (falls back to heuristic)."""
    return bool(
        getattr(settings, "WRITING_QUALITY_SCORE_USE_LLM", True)
        and settings.MODEL_ENABLED
    )


def chapter_outcome_use_llm() -> bool:
    """Whether append_body also runs LLM chapter_summary/events extraction."""
    return bool(
        getattr(settings, "WRITING_CHAPTER_OUTCOME_USE_LLM", False)
        and settings.MODEL_ENABLED
    )


def _resolve_use_llm(use_llm: Optional[bool]) -> bool:
    if use_llm is not None:
        return bool(use_llm) and settings.MODEL_ENABLED
    return quality_score_use_llm()


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


def _merge_llm_rubric(
    heuristic: ChapterQualityRubric,
    result: dict[str, Any],
) -> ChapterQualityRubric:
    """Keep only numeric rubric fields from LLM; ignore notes in rubric dataclass."""
    numeric = {
        k: float(result[k])
        for k in (
            "continuity_score",
            "outline_alignment",
            "character_consistency",
            "duplication_risk",
            "chapter_completion",
            "hook_quality",
        )
        if k in result and result[k] is not None
    }
    merged = {**heuristic.to_dict(), **numeric}
    return ChapterQualityRubric.from_dict(merged)


def score_chapter_quality(
    *,
    chapter_text: str,
    prev_chapter_text: str = "",
    outline_slice: str = "",
    story_bible_excerpt: Optional[dict[str, Any]] = None,
    use_llm: Optional[bool] = None,
    trace_state: Optional[dict[str, Any]] = None,
) -> ChapterQualityRubric:
    """
    Score chapter quality. Default path uses LLM when quality_score_use_llm is enabled.

    Heuristic rubric is always computed first and used as fallback on short text or LLM errors.
    """
    heuristic = score_chapter_quality_heuristic(
        chapter_text=chapter_text,
        prev_chapter_text=prev_chapter_text,
        outline_slice=outline_slice,
        story_bible_excerpt=story_bible_excerpt,
    )
    min_chars = int(getattr(settings, "WRITING_QUALITY_SCORE_LLM_MIN_CHARS", 80))
    if not _resolve_use_llm(use_llm) or len((chapter_text or "").strip()) < min_chars:
        return heuristic
    try:
        from app.services.llm_client import invoke_structured

        payload = {
            "chapter_text": (chapter_text or "")[:12000],
            "prev_chapter_text": (prev_chapter_text or "")[-4000:],
            "outline_for_chapter": (outline_slice or "")[:3000],
            "story_bible": story_bible_excerpt or {},
            "heuristic_hint": heuristic.to_dict(),
        }
        result = invoke_structured(
            "reflection",
            _QUALITY_LLM_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
            trace_state=trace_state,
        )
        if not isinstance(result, dict):
            return heuristic
        return _merge_llm_rubric(heuristic, result)
    except Exception:
        return heuristic


def quality_gate_threshold() -> float:
    return float(getattr(settings, "WRITING_QUALITY_GATE_THRESHOLD", 0.65))


def rubric_passes_gate(rubric: ChapterQualityRubric) -> bool:
    return rubric.pass_gate(threshold=quality_gate_threshold())
