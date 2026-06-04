"""Offline RAG evaluation grouped by retrieval domain."""

from __future__ import annotations

from typing import Any, Callable

from app.services.rag_metrics import evaluate_retrieval

_TOKEN_RE = __import__("re").compile(r"[\w\u4e00-\u9fff]+", __import__("re").UNICODE)
_VALID_DOMAINS = {"writing", "code", "common"}


def _tokenize(text: str) -> set[str]:
    return {t.strip().lower() for t in _TOKEN_RE.findall(text or "") if t.strip()}


def _normalize_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    return d if d in _VALID_DOMAINS else "common"


def context_redundancy_rate(query: str, hits: list[dict[str, Any]]) -> float:
    """
    Estimate irrelevant context ratio in retrieved hits.

    A hit is considered relevant if it shares at least one lexical token
    with the query after normalization.
    """
    if not hits:
        return 0.0
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    irrelevant = 0
    for hit in hits:
        title = str(hit.get("title") or "")
        content = str(hit.get("content") or "")
        tokens = _tokenize(f"{title} {content}")
        if tokens.isdisjoint(query_tokens):
            irrelevant += 1
    return irrelevant / len(hits)


def evaluate_domain_tasks(
    tasks: list[dict[str, Any]],
    *,
    top_k: int = 5,
    search_fn: Callable[[str, str, int], list[dict[str, Any]]],
    query_fn: Callable[[dict[str, Any]], str] | None = None,
    progress_every: int = 0,
    progress_prefix: str = "[rag-eval]",
) -> dict[str, Any]:
    """
    Evaluate retrieval quality by domain.

    task shape:
    {
      "id": "task-1",
      "domain": "writing|code|common",
      "query": "...",
      "relevant_doc_ids": ["doc-1", ...]
    }
    """
    per_task: list[dict[str, Any]] = []
    bucket: dict[str, list[dict[str, float]]] = {d: [] for d in sorted(_VALID_DOMAINS)}
    total = len(tasks)
    for idx, task in enumerate(tasks, start=1):
        domain = _normalize_domain(str(task.get("domain") or "common"))
        query = query_fn(task) if query_fn else str(task.get("query") or "").strip()
        relevant = [str(x) for x in (task.get("relevant_doc_ids") or []) if str(x)]
        hits = search_fn(query, domain, top_k)
        metrics = evaluate_retrieval(hits, relevant, k=top_k)
        redundancy = context_redundancy_rate(query, hits)
        task_result = {
            "id": str(task.get("id") or ""),
            "domain": domain,
            "query": query,
            "metrics": {**metrics, "context_redundancy": redundancy},
            "retrieved_doc_ids": [str(h.get("doc_id")) for h in hits if h.get("doc_id")],
        }
        per_task.append(task_result)
        bucket[domain].append(task_result["metrics"])
        if progress_every > 0 and (idx % progress_every == 0 or idx == total):
            print(f"{progress_prefix} queries {idx}/{total}", flush=True)

    def _avg(items: list[dict[str, float]], key: str) -> float:
        if not items:
            return 0.0
        return sum(float(x.get(key, 0.0)) for x in items) / len(items)

    per_domain: dict[str, dict[str, float]] = {}
    for domain, metrics_list in bucket.items():
        per_domain[domain] = {
            f"recall@{top_k}": _avg(metrics_list, f"recall@{top_k}"),
            "mrr": _avg(metrics_list, "mrr"),
            f"ndcg@{top_k}": _avg(metrics_list, f"ndcg@{top_k}"),
            "context_redundancy": _avg(metrics_list, "context_redundancy"),
            "task_count": float(len(metrics_list)),
        }

    all_metrics = [t["metrics"] for t in per_task]
    summary = {
        f"recall@{top_k}": _avg(all_metrics, f"recall@{top_k}"),
        "mrr": _avg(all_metrics, "mrr"),
        f"ndcg@{top_k}": _avg(all_metrics, f"ndcg@{top_k}"),
        "context_redundancy": _avg(all_metrics, "context_redundancy"),
        "task_count": float(len(per_task)),
    }
    return {"summary": summary, "per_domain": per_domain, "per_task": per_task}


def evaluate_domain_tasks_with_store(
    tasks: list[dict[str, Any]],
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    from app.services.knowledge_store import get_knowledge_store

    store = get_knowledge_store()

    def _search(query: str, domain: str, k: int) -> list[dict[str, Any]]:
        return store.hybrid_search(query, top_k=k, domains={domain})

    return evaluate_domain_tasks(tasks, top_k=top_k, search_fn=_search)
