"""RAG 评估：引用与忠实度；output_guard 按配置合并结果。

从 final_answer 与 retrieved_knowledge 计算 citation、faithfulness。

RAG eval for citations and faithfulness; merged in output_guard when enabled.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[([a-zA-Z0-9\-]{4,})\]")
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?")


def extract_citations(text: str) -> list[str]:
    return list(dict.fromkeys(_CITATION_RE.findall(text)))


def build_citations_from_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for doc in docs:
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        citations.append(
            {
                "doc_id": doc.get("doc_id"),
                "title": doc.get("title"),
                "source_url": meta.get("source_url") or meta.get("url"),
                "chunk_index": meta.get("chunk_index", 0),
                "score": doc.get("rerank_score") or doc.get("score") or doc.get("rrf_score"),
            }
        )
    return citations


def merge_citations(answer: str, retrieved_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map [doc_id] markers in answer to document metadata."""
    cited_ids = set(extract_citations(answer))
    by_id = {str(d.get("doc_id")): d for d in retrieved_docs if d.get("doc_id")}
    out: list[dict[str, Any]] = []
    for doc_id in cited_ids:
        doc = by_id.get(doc_id)
        if not doc:
            continue
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        out.append(
            {
                "doc_id": doc_id,
                "title": doc.get("title"),
                "source_url": meta.get("source_url") or meta.get("url"),
                "chunk_index": meta.get("chunk_index", 0),
            }
        )
    if not out and retrieved_docs:
        return build_citations_from_docs(retrieved_docs[:3])
    return out


def _claim_supported(claim: str, docs: list[dict[str, Any]]) -> bool:
    claim_tokens = [t for t in claim.lower().split() if len(t) > 3]
    if not claim_tokens:
        return True
    corpus = " ".join(
        f"{d.get('title', '')} {d.get('content', '')}" for d in docs
    ).lower()
    hits = sum(1 for t in claim_tokens if t in corpus)
    return hits >= len(claim_tokens)


def check_faithfulness(answer: str, retrieved_docs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Return {"faithful": bool, "score": float, "unsupported_claims": list[str]}.
    Uses LLM when enabled; otherwise lexical overlap heuristics.
    """
    if not answer.strip():
        return {"faithful": True, "score": 1.0, "unsupported_claims": []}
    if not retrieved_docs:
        return {"faithful": False, "score": 0.0, "unsupported_claims": ["no retrieved context"]}

    if settings.RAG_FAITHFULNESS_LLM_ENABLED and settings.MODEL_ENABLED:
        try:
            return _llm_faithfulness(answer, retrieved_docs)
        except Exception as exc:
            logger.warning("LLM faithfulness check failed: %s", exc)

    unsupported: list[str] = []
    sentences = [s.strip() for s in _SENTENCE_RE.findall(answer) if len(s.strip()) > 12]
    for sentence in sentences[:12]:
        if not _claim_supported(sentence, retrieved_docs):
            unsupported.append(sentence[:200])
    score = 1.0 - (len(unsupported) / max(len(sentences), 1))
    faithful = score >= settings.RAG_FAITHFULNESS_THRESHOLD
    _observe_faithfulness(score)
    return {"faithful": faithful, "score": round(score, 3), "unsupported_claims": unsupported}


def _llm_faithfulness(answer: str, retrieved_docs: list[dict[str, Any]]) -> dict[str, Any]:
    from app.services.llm_client import invoke_structured

    context = [
        {
            "doc_id": d.get("doc_id"),
            "title": d.get("title"),
            "content": (d.get("content") or "")[:1500],
        }
        for d in retrieved_docs[:8]
    ]
    system = (
        'Judge if the answer is supported by the documents. Return JSON: '
        '{"faithful": bool, "score": 0-1, "unsupported_claims": ["..."]}'
    )
    user = json.dumps({"answer": answer[:4000], "documents": context}, ensure_ascii=False)
    result = invoke_structured("rag_eval", system, user)
    score = float(result.get("score", 0.0))
    faithful = bool(result.get("faithful", score >= settings.RAG_FAITHFULNESS_THRESHOLD))
    unsupported = [str(x) for x in result.get("unsupported_claims") or []]
    _observe_faithfulness(score)
    return {"faithful": faithful, "score": score, "unsupported_claims": unsupported}


def citation_coverage(answer: str, retrieved_docs: list[dict[str, Any]]) -> float:
    if not retrieved_docs:
        return 0.0
    cited = set(extract_citations(answer))
    if not cited:
        return 0.0
    doc_ids = {str(d.get("doc_id")) for d in retrieved_docs if d.get("doc_id")}
    if not doc_ids:
        return 0.0
    return len(cited & doc_ids) / len(doc_ids)


def _observe_faithfulness(score: float) -> None:
    if not settings.METRICS_ENABLED:
        return
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().observe_rag_faithfulness(score)
    except Exception:
        pass
