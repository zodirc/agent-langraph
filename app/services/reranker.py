"""Reranking: lexical (default), optional cross-encoder, or Cohere API."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

from app.config.settings import settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

_cross_encoder_model: Any = None


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1}


def _lexical_score(query: str, doc: dict[str, Any]) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return float(doc.get("score") or doc.get("rrf_score") or 0.0)
    haystack = f"{doc.get('title', '')} {doc.get('content', '')}".lower()
    overlap = sum(1 for t in q_tokens if t in haystack)
    base = overlap / len(q_tokens)
    prior = float(doc.get("rrf_score") or doc.get("score") or 0.0)
    return 0.7 * base + 0.3 * min(prior, 1.0)


def _get_cross_encoder():
    global _cross_encoder_model
    if _cross_encoder_model is not None:
        return _cross_encoder_model
    try:
        from sentence_transformers import CrossEncoder

        model_name = settings.RAG_RERANK_MODEL or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        _cross_encoder_model = CrossEncoder(model_name)
        return _cross_encoder_model
    except ImportError:
        logger.info("sentence-transformers not installed; cross-encoder rerank unavailable")
        return None
    except Exception as exc:
        logger.warning("failed to load cross-encoder: %s", exc)
        return None


def _cross_encoder_score(query: str, docs: list[dict[str, Any]]) -> list[float]:
    model = _get_cross_encoder()
    if model is None:
        return [_lexical_score(query, d) for d in docs]
    pairs = [
        (query, f"{d.get('title', '')} {d.get('content', '')}"[:2000]) for d in docs
    ]
    scores = model.predict(pairs)
    return [float(s) for s in scores]


def _cohere_rerank(query: str, docs: list[dict[str, Any]]) -> list[float]:
    import httpx

    api_key = settings.RAG_COHERE_API_KEY or settings.EMBEDDING_API_KEY
    if not api_key:
        raise ValueError("cohere rerank requires RAG_COHERE_API_KEY or VOYAGE_API_KEY")
    documents = [f"{d.get('title', '')}\n{d.get('content', '')}"[:4000] for d in docs]
    response = httpx.post(
        "https://api.cohere.com/v1/rerank",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.RAG_COHERE_RERANK_MODEL,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    scores = [0.0] * len(docs)
    for item in data.get("results", []):
        idx = int(item.get("index", 0))
        if 0 <= idx < len(scores):
            scores[idx] = float(item.get("relevance_score", 0.0))
    return scores


def _score_fn() -> Callable[[str, list[dict[str, Any]]], list[float]]:
    backend = settings.RAG_RERANK_BACKEND.lower()
    if backend == "cross_encoder":
        return _cross_encoder_score
    if backend == "cohere":
        return lambda q, docs: _cohere_rerank(q, docs)
    return lambda q, docs: [_lexical_score(q, d) for d in docs]


def rerank(query: str, docs: list[dict[str, Any]], *, top_k: int | None = None) -> list[dict[str, Any]]:
    """Re-order documents; falls back to lexical on any backend failure."""
    if not docs:
        return []
    limit = top_k or settings.RETRIEVAL_TOP_K
    if not settings.RAG_RERANK_ENABLED:
        return docs[:limit]

    started = time.perf_counter()
    try:
        score_docs = _score_fn()
        scores = score_docs(query, docs)
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc, score in zip(docs, scores):
            copy = dict(doc)
            copy["rerank_score"] = score
            scored.append((score, copy))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        result = [doc for _, doc in scored[:limit]]
        _observe_rerank_latency(time.perf_counter() - started, backend=settings.RAG_RERANK_BACKEND)
        return result
    except Exception as exc:
        logger.warning("rerank backend %s failed: %s", settings.RAG_RERANK_BACKEND, exc)
        scored = [( _lexical_score(query, d), {**d, "rerank_score": _lexical_score(query, d)}) for d in docs]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [doc for _, doc in scored[:limit]]


def _observe_rerank_latency(seconds: float, *, backend: str) -> None:
    if not settings.METRICS_ENABLED:
        return
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().observe_rag_rerank_latency(seconds, backend=backend)
    except Exception:
        pass
