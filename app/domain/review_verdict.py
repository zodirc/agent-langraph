"""ReviewVerdict — canonical chapter qualification semantics (OMAW)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReviewEvidence:
    outline_slice: bool = False
    prev_chapter: bool = False
    rag_used: bool = False
    react_steps: int = 0
    knowledge_used: bool = False
    memory_used: bool = False
    tools_used: bool = False
    fact_bundle_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outline_slice": self.outline_slice,
            "prev_chapter": self.prev_chapter,
            "rag_used": self.rag_used,
            "react_steps": self.react_steps,
            "knowledge_used": self.knowledge_used,
            "memory_used": self.memory_used,
            "tools_used": self.tools_used,
            "fact_bundle_id": self.fact_bundle_id,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> ReviewEvidence:
        if not isinstance(data, dict):
            return cls()
        return cls(
            outline_slice=bool(data.get("outline_slice")),
            prev_chapter=bool(data.get("prev_chapter")),
            rag_used=bool(data.get("rag_used")),
            react_steps=int(data.get("react_steps") or 0),
            knowledge_used=bool(data.get("knowledge_used")),
            memory_used=bool(data.get("memory_used")),
            tools_used=bool(data.get("tools_used")),
            fact_bundle_id=str(data.get("fact_bundle_id") or ""),
        )


@dataclass
class ReviewVerdict:
    chapter_index: int
    qualified: bool
    model_pass: bool
    rubric: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    polish_recommended: bool = False
    evidence: ReviewEvidence = field(default_factory=ReviewEvidence)
    summary: str = ""
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_index": self.chapter_index,
            "qualified": self.qualified,
            "model_pass": self.model_pass,
            "rubric": dict(self.rubric),
            "issues": list(self.issues),
            "polish_recommended": self.polish_recommended,
            "evidence": self.evidence.to_dict(),
            "summary": self.summary,
            "at": self.at or _now_iso(),
            # Legacy compat for work_item_satisfied / UI
            "pass": self.qualified,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional[ReviewVerdict]:
        if not isinstance(data, dict):
            return None
        ev = ReviewEvidence.from_dict(data.get("evidence"))
        return cls(
            chapter_index=int(data.get("chapter_index") or 0),
            qualified=bool(data.get("qualified", data.get("pass", False))),
            model_pass=bool(data.get("model_pass", data.get("pass", False))),
            rubric=dict(data.get("rubric") or data.get("chapter_quality") or {}),
            issues=[str(x) for x in (data.get("issues") or []) if x],
            polish_recommended=bool(data.get("polish_recommended")),
            evidence=ev,
            summary=str(data.get("summary") or ""),
            at=str(data.get("at") or ""),
        )

    def is_valid(self) -> bool:
        """ADR: verdict without fact_bundle_id is not valid."""
        return bool(self.evidence.fact_bundle_id.strip())

    @classmethod
    def from_phase_result(
        cls,
        *,
        chapter_index: int,
        phase_result: dict[str, Any],
        rubric_dict: dict[str, Any],
        fact_bundle_id: str,
        rag_meta: Optional[dict[str, Any]] = None,
    ) -> ReviewVerdict:
        rag_meta = rag_meta or {}
        issues = [str(x) for x in (phase_result.get("issues") or []) if x]
        model_pass = bool(phase_result.get("pass", True))
        blocking = any(
            kw in (i.lower())
            for i in issues
            for kw in ("blocking", "empty", "fatal", "严重")
        )
        qualified = model_pass and not blocking
        polish_rec = bool(phase_result.get("polish_recommended"))
        ev = ReviewEvidence(
            outline_slice=bool(rag_meta.get("outline_slice")),
            prev_chapter=bool(rag_meta.get("prev_chapter")),
            rag_used=bool(rag_meta.get("rag_used")),
            react_steps=int(rag_meta.get("react_steps") or 0),
            knowledge_used=bool(rag_meta.get("knowledge_used")),
            memory_used=bool(rag_meta.get("memory_used")),
            tools_used=bool(rag_meta.get("tools_used")),
            fact_bundle_id=fact_bundle_id,
        )
        return cls(
            chapter_index=chapter_index,
            qualified=qualified,
            model_pass=model_pass,
            rubric=dict(rubric_dict),
            issues=issues,
            polish_recommended=polish_rec,
            evidence=ev,
            summary=str(phase_result.get("summary") or ""),
        )


def save_review_verdict(
    task_id: str,
    verdict: ReviewVerdict,
    *,
    agent: str = "reviewer",
) -> None:
    """Only reviewer worker may write chapter_reviews (ADR 5.3)."""
    if agent != "reviewer":
        raise PermissionError(f"only reviewer may save ReviewVerdict, not {agent}")
    from app.services.writing_phases import _chapter_key, load_chapter_reviews, save_chapter_reviews

    reviews = load_chapter_reviews(task_id)
    rev_map = dict(reviews.get("reviews") or {})
    rev_map[_chapter_key(verdict.chapter_index)] = verdict.to_dict()
    reviews["reviews"] = rev_map
    save_chapter_reviews(task_id, reviews)


def load_review_verdict(task_id: str, chapter_index: int) -> Optional[ReviewVerdict]:
    from app.services.writing_phases import _chapter_key, load_chapter_reviews

    reviews = load_chapter_reviews(task_id)
    raw = (reviews.get("reviews") or {}).get(_chapter_key(chapter_index))
    return ReviewVerdict.from_dict(raw)
