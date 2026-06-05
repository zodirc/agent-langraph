"""Persist retrieval/tool outputs as ContextItem registry on AgentState (ADR §9.3)."""

from __future__ import annotations

from typing import Any

from app.runtime.state import AgentState, merge_state
from app.services.context_fingerprint import strict_dedupe_key
from app.services.context_items import ContextItem, new_context_id

REGISTRY_KEY = "context_item_registry"
_MAX_REGISTRY = 48


def _registry_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    raw = state.get(REGISTRY_KEY) or []
    return list(raw) if isinstance(raw, list) else []


def items_from_knowledge(docs: list[dict[str, Any]]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        text = str(doc.get("content") or doc.get("text") or "")[:3000]
        if not text:
            continue
        items.append(
            ContextItem(
                id=new_context_id("rk"),
                kind="knowledge",
                source="retrieval",
                role="system",
                content=text,
                priority="medium",
                bucket="retrieved_knowledge",
                meta={
                    "doc_id": doc.get("doc_id") or doc.get("id"),
                    "source": doc.get("source"),
                    "score": doc.get("score"),
                },
            )
        )
    return items


def items_from_memory_hits(hits: list[dict[str, Any]]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("summary") or hit.get("content") or hit)[:2000]
        if not text:
            continue
        items.append(
            ContextItem(
                id=new_context_id("rm"),
                kind="episodic_memory",
                source="memory",
                role="system",
                content=text,
                priority="medium",
                bucket="retrieved_memory",
                meta={"memory_id": hit.get("id"), "score": hit.get("score")},
            )
        )
    return items


def items_from_tool_results(tools: list[dict[str, Any]]) -> list[ContextItem]:
    items: list[ContextItem] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("tool") or tool.get("name") or "tool"
        status = tool.get("status", "?")
        result = tool.get("result")
        snippet = str(result)[:1200] if result is not None else ""
        items.append(
            ContextItem(
                id=new_context_id("rt"),
                kind="tool_output",
                source="tool",
                role="tool",
                content=f"[{name}] {status}: {snippet}",
                priority="high",
                bucket="tool_observations",
                meta={"tool": name, "status": status},
            )
        )
    return items


def merge_registry_items(
    state: AgentState | dict[str, Any],
    new_items: list[ContextItem],
) -> AgentState:
    """Append normalized items; dedupe by source-aware fingerprint."""
    if not new_items:
        return state  # type: ignore[return-value]
    existing = _registry_from_state(state)
    seen = {strict_dedupe_key(e) for e in existing if isinstance(e, dict)}
    for item in new_items:
        key = strict_dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        existing.append(item.to_dict())
    trimmed = existing[-_MAX_REGISTRY:]
    return merge_state(state, **{REGISTRY_KEY: trimmed})  # type: ignore[arg-type]


def registry_items_for_collection(state: dict[str, Any]) -> list[ContextItem]:
    out: list[ContextItem] = []
    for raw in _registry_from_state(state):
        if not isinstance(raw, dict):
            continue
        try:
            out.append(
                ContextItem(
                    id=str(raw.get("id") or new_context_id()),
                    kind=raw.get("kind") or "knowledge",  # type: ignore[arg-type]
                    source=raw.get("source") or "state",  # type: ignore[arg-type]
                    role=str(raw.get("role") or "system"),
                    content=str(raw.get("content") or ""),
                    priority=raw.get("priority") or "medium",  # type: ignore[arg-type]
                    estimated_tokens=int(raw.get("estimated_tokens") or 0),
                    compressible=bool(raw.get("compressible", True)),
                    droppable=bool(raw.get("droppable", True)),
                    bucket=raw.get("bucket"),  # type: ignore[arg-type]
                    meta=dict(raw.get("meta") or {}),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def persist_retrieval_context(state: AgentState, *, knowledge: list, memories: list) -> AgentState:
    items = items_from_knowledge(knowledge) + items_from_memory_hits(memories)
    updated = merge_registry_items(state, items)
    try:
        from app.services.metrics_service import get_metrics_service

        if knowledge:
            get_metrics_service().inc_context_recall("retrieval", len(knowledge))
        if memories:
            get_metrics_service().inc_context_recall("memory", len(memories))
    except Exception:
        pass
    return updated


def persist_tool_context(state: AgentState, tool_results: list) -> AgentState:
    items = items_from_tool_results(tool_results)
    updated = merge_registry_items(state, items)
    try:
        from app.services.metrics_service import get_metrics_service

        if items:
            get_metrics_service().inc_context_recall("tool", len(items))
    except Exception:
        pass
    return updated
