"""Token-aware context cost estimation (ADR §9.2)."""

from __future__ import annotations

from app.services.context_items import ContextItem
from app.services.resource_budget import _estimate_tokens


def estimate_text_tokens(text: str, *, model_name: str = "") -> int:
    """Heuristic token estimate; model_name reserved for provider-specific tuning."""
    _ = model_name
    return _estimate_tokens(text or "")


def estimate_item_tokens(item: ContextItem, *, model_name: str = "") -> ContextItem:
    if item.estimated_tokens <= 0 and item.content:
        item.estimated_tokens = estimate_text_tokens(item.content, model_name=model_name)
    return item


def estimate_items(items: list[ContextItem], *, model_name: str = "") -> list[ContextItem]:
    return [estimate_item_tokens(i, model_name=model_name) for i in items]


def sum_item_tokens(items: list[ContextItem]) -> int:
    return sum(max(0, int(i.estimated_tokens)) for i in items)
