"""Audit trail for context assembly: kept / compressed / dropped (ADR §11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.context_items import ContextBucketName, ContextItem


@dataclass
class ContextTraceAction:
    action: str  # kept | compressed | dropped
    item_id: str
    bucket: ContextBucketName
    reason: str
    tokens_before: int = 0
    tokens_after: int = 0
    method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "item_id": self.item_id,
            "bucket": self.bucket,
            "reason": self.reason,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "method": self.method,
        }


@dataclass
class ContextAssemblyTrace:
    purpose: str
    actions: list[ContextTraceAction] = field(default_factory=list)
    compression_receipts: list[dict[str, Any]] = field(default_factory=list)

    def record_kept(self, item: ContextItem, *, reason: str = "within_budget") -> None:
        self.actions.append(
            ContextTraceAction(
                action="kept",
                item_id=item.id,
                bucket=item.resolve_bucket(),
                reason=reason,
                tokens_before=item.estimated_tokens,
                tokens_after=item.estimated_tokens,
            )
        )

    def record_compressed(
        self,
        item: ContextItem,
        *,
        reason: str,
        tokens_after: int,
        method: str = "semantic",
        receipt: dict[str, Any] | None = None,
    ) -> None:
        self.actions.append(
            ContextTraceAction(
                action="compressed",
                item_id=item.id,
                bucket=item.resolve_bucket(),
                reason=reason,
                tokens_before=item.estimated_tokens,
                tokens_after=tokens_after,
                method=method,
            )
        )
        if receipt:
            self.compression_receipts.append(
                {
                    "item_id": item.id,
                    "bucket": item.resolve_bucket(),
                    "method": method,
                    "reason": reason,
                    **receipt,
                }
            )

    def record_dropped(self, item: ContextItem, *, reason: str) -> None:
        self.actions.append(
            ContextTraceAction(
                action="dropped",
                item_id=item.id,
                bucket=item.resolve_bucket(),
                reason=reason,
                tokens_before=item.estimated_tokens,
                tokens_after=0,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        kept = sum(1 for a in self.actions if a.action == "kept")
        compressed = sum(1 for a in self.actions if a.action == "compressed")
        dropped = sum(1 for a in self.actions if a.action == "dropped")
        return {
            "purpose": self.purpose,
            "kept_count": kept,
            "compressed_count": compressed,
            "dropped_count": dropped,
            "actions": [a.to_dict() for a in self.actions],
            "compression_receipts": list(self.compression_receipts),
        }


def build_prompt_composition_view(
    *,
    purpose: str,
    bucket_allocations: list[dict[str, Any]],
    items_kept: list[ContextItem],
    items_compressed: list[ContextItem],
    items_dropped: list[ContextItem],
    rendered_messages: list[dict[str, Any]],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Developer-facing prompt composition debug view (ADR §11.3)."""

    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for item in items_kept:
        b = item.resolve_bucket()
        by_bucket.setdefault(b, []).append(
            {
                "id": item.id,
                "kind": item.kind,
                "priority": item.priority,
                "tokens": item.estimated_tokens,
                "preview": (item.content or "")[:200],
            }
        )
    return {
        "purpose": purpose,
        "buckets": bucket_allocations,
        "items_by_bucket": by_bucket,
        "compressed": [i.to_dict() for i in items_compressed[:30]],
        "dropped": [i.to_dict() for i in items_dropped[:30]],
        "rendered_message_count": len(rendered_messages),
        "rendered_roles": [m.get("role") for m in rendered_messages[:20]],
        "trace": trace,
    }
