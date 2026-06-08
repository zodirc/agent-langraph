"""Citation and claim-to-evidence grounding validation."""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings
from app.runtime.evidence_models import (
    AnswerMode,
    ClaimBinding,
    EvidencePacket,
    FailureTag,
    GroundingCheckResult,
)
from app.services.rag_eval import extract_citations, merge_citations

_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")
_CITATION_RE = re.compile(r"\[([a-zA-Z0-9_\-]{4,})\]")


def _claim_supported(
    claim: str,
    evidence_text: str,
    *,
    support_ratio: float = 0.5,
    min_token_len: int = 4,
) -> tuple[bool, str]:
    tokens = [
        t
        for t in re.findall(r"[\w\u4e00-\u9fff]+", claim.lower())
        if len(t) >= min_token_len
    ]
    if not tokens:
        return True, "trivial"
    corpus = evidence_text.lower()
    hits = sum(1 for t in tokens if t in corpus)
    ratio = hits / len(tokens)
    if ratio >= support_ratio:
        return True, "direct" if ratio >= 0.75 else "contextual"
    return False, "none"


def _high_value_claims(text: str) -> list[tuple[str, str]]:
    """Extract conclusion-like sentences for grounding check."""
    claims: list[tuple[str, str]] = []
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if len(s.strip()) > 12]
    for idx, sentence in enumerate(sentences[:12]):
        lower = sentence.lower()
        if re.search(
            r"(?i)\b(therefore|thus|结论|建议|应该|修复|原因是|答案是|steps?)\b",
            lower,
        ) or sentence.endswith(("。", ".", "!", "?")):
            claims.append((f"claim_{idx}", sentence[:300]))
    if not claims and sentences:
        claims.append(("claim_0", sentences[0][:300]))
    return claims


def _packets_corpus(packets: list[EvidencePacket], hits: list[dict[str, Any]]) -> str:
    parts = [p.snippet_text for p in packets if p.snippet_text]
    if not parts:
        parts = [
            str(h.get("content") or "")
            for h in hits
            if isinstance(h, dict) and h.get("content")
        ]
    return "\n".join(parts)


def _injected_ids(packets: list[EvidencePacket], hits: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for p in packets:
        if p.chunk_id:
            ids.add(str(p.chunk_id))
        if p.source_id:
            ids.add(str(p.source_id))
    for h in hits:
        if h.get("doc_id"):
            ids.add(str(h["doc_id"]))
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        if meta.get("parent_doc_id"):
            ids.add(str(meta["parent_doc_id"]))
    return ids


def check_grounding(
    answer: str,
    *,
    hits: list[dict[str, Any]] | None = None,
    packets: list[EvidencePacket] | None = None,
    answer_mode: str = AnswerMode.BEST_EFFORT_GROUNDED.value,
    support_ratio: float = 0.5,
    min_token_len: int = 4,
) -> GroundingCheckResult:
    """Validate claims and citations against injected evidence."""
    strictness = str(getattr(settings, "RETRIEVAL_CITATION_CHECK_STRICTNESS", "basic")).lower()
    if strictness == "off":
        return GroundingCheckResult(grounded=True, score=1.0, answer_mode=answer_mode)

    hits = hits or []
    packets = packets or []
    if not answer.strip():
        return GroundingCheckResult(grounded=True, score=1.0, answer_mode=answer_mode)

    corpus = _packets_corpus(packets, hits)
    injected = _injected_ids(packets, hits)
    failure_tags: list[str] = []
    bindings: list[ClaimBinding] = []
    unsupported: list[str] = []
    fake_citations: list[str] = []

    cited = extract_citations(answer)
    for cite_id in cited:
        if cite_id not in injected:
            fake_citations.append(cite_id)
            failure_tags.append(FailureTag.FAKE_CITATION.value)

    for claim_id, claim_text in _high_value_claims(answer):
        supported, strength = _claim_supported(
            claim_text,
            corpus,
            support_ratio=support_ratio,
            min_token_len=min_token_len,
        )
        cite_id = ""
        cite_match = _CITATION_RE.search(claim_text)
        if cite_match:
            cite_id = cite_match.group(1)
        bindings.append(
            ClaimBinding(
                claim_id=claim_id,
                claim_text=claim_text,
                citation_id=cite_id,
                support_strength=strength,
                supported=supported,
            )
        )
        if not supported:
            unsupported.append(claim_text)

    if not corpus and hits == [] and packets == []:
        unsupported.append("no injected evidence")
        failure_tags.append(FailureTag.UNSUPPORTED_GENERATION.value)

    if unsupported:
        failure_tags.append(FailureTag.UNSUPPORTED_GENERATION.value)

    score = 1.0 - (len(unsupported) / max(len(bindings), 1))
    grounded = score >= float(getattr(settings, "RAG_FAITHFULNESS_THRESHOLD", 0.7))

    if answer_mode == AnswerMode.STRICT_GROUNDED.value and unsupported:
        grounded = False
    if answer_mode == AnswerMode.REFUSE_IF_INSUFFICIENT.value and (unsupported or fake_citations):
        grounded = False

    return GroundingCheckResult(
        grounded=grounded,
        score=round(score, 3),
        answer_mode=answer_mode,
        bindings=bindings,
        unsupported_claims=unsupported[:8],
        fake_citations=fake_citations,
        failure_tags=list(dict.fromkeys(failure_tags)),
        debug_info={"cited_ids": cited, "injected_count": len(injected)},
    )


def check_tool_observation_grounding(
    answer: str,
    *,
    hits: list[dict[str, Any]] | None = None,
    answer_mode: str = AnswerMode.BEST_EFFORT_GROUNDED.value,
) -> GroundingCheckResult:
    """Lenient grounding for summaries after successful tool side effects."""
    hits = hits or []
    if not answer.strip():
        return GroundingCheckResult(grounded=True, score=1.0, answer_mode=answer_mode)
    if not hits:
        return GroundingCheckResult(grounded=True, score=1.0, answer_mode=answer_mode)
    return check_grounding(
        answer,
        hits=hits,
        packets=[],
        answer_mode=answer_mode,
        support_ratio=0.2,
        min_token_len=2,
    )


def grounding_to_faithfulness(result: GroundingCheckResult) -> dict[str, Any]:
    """Bridge to legacy rag_eval faithfulness dict shape."""
    return {
        "faithful": result.grounded,
        "score": result.score,
        "unsupported_claims": result.unsupported_claims,
        "fake_citations": result.fake_citations,
        "bindings": [b.model_dump() for b in result.bindings],
        "failure_tags": result.failure_tags,
    }


def merge_grounding_citations(
    answer: str,
    hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return merge_citations(answer, hits)
