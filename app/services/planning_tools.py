from __future__ import annotations

from app.services.tool_registry import get_tool_registry

# 由 Retrieval 节点负责，不是 Tool Registry 中的工具名
_RETRIEVAL_ALIASES = frozenset(
    {
        "search",
        "knowledge_search",
        "retrieve",
        "retrieval",
        "web_search",
        "knowledge",
        "rag",
        "hybrid_search",
    }
)


def normalize_selected_tools(raw_tools: list[str]) -> tuple[list[str], list[str]]:
    """
    Keep only registered tools; drop aliases that map to knowledge retrieval.
    Returns (accepted, dropped).
    """
    registry = get_tool_registry()
    available = set(registry.list_tools())
    accepted: list[str] = []
    dropped: list[str] = []

    for item in raw_tools:
        name = str(item).strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in _RETRIEVAL_ALIASES:
            dropped.append(name)
            continue
        if name in available:
            if name not in accepted:
                accepted.append(name)
        elif lowered in available:
            if lowered not in accepted:
                accepted.append(lowered)
        else:
            dropped.append(name)

    return accepted, dropped

