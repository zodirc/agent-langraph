"""RAG relevance gate: threshold filtering, metadata annotation, snippet extraction.

Turns rerank from a sorter into an admission controller for prompt injection.
"""

from __future__ import annotations

import re
from typing import Any

from app.config.settings import settings

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")

_PURPOSE_MIN_SCORE: dict[str, float] = {
    "reasoning": 0.35,
    "writing": 0.25,
    "reviewing": 0.30,
    "planning": 0.40,
    "routing": 0.40,
    "intent_observation": 1.0,
}

_PURPOSE_MAX_KNOWLEDGE: dict[str, int] = {
    "reasoning": 8,
    "writing": 10,
    "reviewing": 8,
    "planning": 4,
    "routing": 3,
    "intent_observation": 0,
}


def relevance_score(hit: dict[str, Any]) -> float:
    return float(
        hit.get("relevance_score")
        or hit.get("rerank_score")
        or hit.get("score")
        or hit.get("rrf_score")
        or 0.0
    )


def _effective_threshold(hits: list[dict[str, Any]]) -> float:
    """Compute min score: absolute for cross_encoder/cohere, relative for lexical."""
    if not getattr(settings, "RAG_RELEVANCE_GATE_ENABLED", True):
        return 0.0
    min_score = float(getattr(settings, "RAG_RERANK_MIN_SCORE", 0.0))
    if min_score <= 0:
        return 0.0
    if not hits:
        return min_score
    backend = str(getattr(settings, "RAG_RERANK_BACKEND", "lexical")).lower()
    if backend in ("cross_encoder", "cohere"):
        return min_score
    top = max(relevance_score(h) for h in hits)
    ratio = float(getattr(settings, "RAG_RELEVANCE_RELATIVE_RATIO", 0.45))
    relative = top * ratio if top > 0 else 0.0
    return max(min_score, relative)


def annotate_hit(
    hit: dict[str, Any],
    *,
    stage: str,
    query: str = "",
    threshold: float | None = None,
) -> dict[str, Any]:
    """Add unified relevance metadata to a retrieval hit."""
    copy = dict(hit)
    score = relevance_score(copy)
    copy["relevance_score"] = score
    copy["retrieval_stage"] = stage
    if threshold is None:
        threshold = 0.0
    if score >= threshold:
        copy["relevance_passed"] = True
        copy["relevance_reason"] = "above_threshold"
    elif copy.get("source") == "adjacency":
        copy["relevance_passed"] = False
        copy["relevance_reason"] = "adjacency_supplement"
    else:
        copy["relevance_passed"] = False
        copy["relevance_reason"] = "below_threshold"
    return copy


def apply_relevance_gate(
    hits: list[dict[str, Any]],
    query: str,
    *,
    stage: str = "rerank",
) -> list[dict[str, Any]]:
    """Filter hits by score threshold; fallback-fill to min_relevant_hits."""
    if not hits:
        return []
    threshold = _effective_threshold(hits)
    min_hits = max(0, int(getattr(settings, "RAG_MIN_RELEVANT_HITS", 1)))
    scored = sorted(hits, key=relevance_score, reverse=True)
    passed = [h for h in scored if relevance_score(h) >= threshold]
    if len(passed) < min_hits:
        passed = scored[: max(min_hits, len(passed))]
        reason = "fallback_min_hits"
    else:
        reason = "above_threshold"
    result: list[dict[str, Any]] = []
    passed_ids = {str(h.get("doc_id")) for h in passed}
    for hit in scored:
        annotated = annotate_hit(hit, stage=stage, query=query, threshold=threshold)
        if str(hit.get("doc_id")) in passed_ids:
            annotated["relevance_passed"] = True
            if annotated["relevance_reason"] == "below_threshold":
                annotated["relevance_reason"] = reason
        result.append(annotated)
    return [h for h in result if h.get("relevance_passed")]


def purpose_min_score(purpose: str) -> float:
    base = _effective_threshold([])
    purpose_floor = _PURPOSE_MIN_SCORE.get(purpose, 0.0)
    if purpose == "intent_observation":
        return 1.0
    if purpose_floor <= 0:
        return base
    return max(base, purpose_floor)


def purpose_max_knowledge(purpose: str) -> int:
    return _PURPOSE_MAX_KNOWLEDGE.get(purpose, 12)


def should_inject_knowledge(doc: dict[str, Any], *, purpose: str) -> tuple[bool, str, str]:
    """Return (inject, priority, droppable) for a knowledge hit."""
    if purpose == "intent_observation":
        return False, "low", True
    score = relevance_score(doc)
    floor = purpose_min_score(purpose)
    passed = bool(doc.get("relevance_passed", score >= floor))
    is_adjacency = doc.get("source") == "adjacency" or doc.get("adjacency_only")
    if purpose in ("reasoning", "reviewing"):
        if not passed or is_adjacency:
            return False, "low", True
        return True, "medium", False
    if purpose == "writing":
        if not passed:
            return True, "low", True
        if is_adjacency:
            return True, "low", True
        return True, "medium", False
    if purpose in ("planning", "routing"):
        if not passed:
            return False, "low", True
        return True, "low", True
    if not passed:
        return True, "low", True
    return True, "medium", True


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1}


def _sentence_score(sentence: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    lower = sentence.lower()
    hits = sum(1 for t in query_tokens if t in lower)
    return hits / len(query_tokens)


def extract_evidence_snippet(
    text: str,
    query: str,
    *,
    title: str = "",
    max_sentences: int | None = None,
) -> str:
    """Keep the most query-relevant sentences instead of injecting full chunk."""
    if not getattr(settings, "RAG_SNIPPET_ENABLED", True):
        return text
    limit = max_sentences or int(getattr(settings, "RAG_SNIPPET_MAX_SENTENCES", 5))
    limit = max(2, min(limit, 8))
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text) if len(s.strip()) > 8]
    if len(sentences) <= limit:
        return text
    q_tokens = _tokenize(query)
    if not q_tokens:
        return text[:3000]
    scored = [(i, _sentence_score(s, q_tokens), s) for i, s in enumerate(sentences)]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]
    top.sort(key=lambda x: x[0])
    body = " ".join(s for _, _, s in top)
    prefix = f"[{title}] " if title else ""
    return f"{prefix}{body}".strip()


def gate_stats(hits: list[dict[str, Any]], *, injected: int = 0) -> dict[str, Any]:
    """Observability bundle for retrieval noise metrics."""
    total = len(hits)
    passed = sum(1 for h in hits if h.get("relevance_passed"))
    adjacency = sum(1 for h in hits if h.get("source") == "adjacency")
    low_score = sum(
        1 for h in hits if h.get("relevance_passed") and relevance_score(h) < 0.3
    )
    return {
        "retrieved_total": total,
        "threshold_passed": passed,
        "injected_knowledge": injected,
        "adjacency_count": adjacency,
        "adjacency_ratio": adjacency / total if total else 0.0,
        "low_score_injected_rate": low_score / injected if injected else 0.0,
    }
