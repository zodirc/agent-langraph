"""Render ContextEnvelope messages — sole assembly path for governed prompts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.context_estimator import sum_item_tokens
from app.services.context_items import (
    ContextBucketAllocation,
    ContextEnvelope,
    ContextItem,
    ContextPurpose,
)
from app.services.context_policy import PromptContextPolicy
from app.services.context_reducer import reduce_context_items
from app.services.context_trace import ContextAssemblyTrace, build_prompt_composition_view
from app.services.context_estimator import estimate_items


_BUCKET_RENDER_ORDER: list[str] = [
    "semantic_summary",
    "working_memory",
    "current_turn",
    "recent_transcript",
    "retrieved_memory",
    "retrieved_knowledge",
    "tool_observations",
    "file_context",
    "diagnostics",
    "system_policy",
]


def _items_to_messages(items: list[ContextItem]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in items:
        if not (item.content or "").strip():
            continue
        msg: dict[str, Any] = {"role": item.role, "content": item.content}
        if item.meta.get("at"):
            msg["at"] = item.meta["at"]
        if item.meta:
            msg["meta"] = {
                k: v
                for k, v in item.meta.items()
                if k not in ("at", "summary")
            }
        messages.append(msg)
    return messages


def assemble_context_envelope(
    items: list[ContextItem],
    *,
    purpose: ContextPurpose,
    policy: PromptContextPolicy,
    token_budget_total: int,
    model_name: str = "",
    state: dict[str, Any] | None = None,
) -> ContextEnvelope:
    items = estimate_items(items, model_name=model_name)
    trace = ContextAssemblyTrace(purpose=purpose)

    initial_by_bucket: dict[str, int] = defaultdict(int)
    for item in items:
        initial_by_bucket[item.resolve_bucket()] += item.estimated_tokens

    kept, compressed, dropped = reduce_context_items(
        items,
        policy,
        token_budget_total=token_budget_total,
        state=state,
        trace=trace,
    )

    final_by_bucket: dict[str, int] = defaultdict(int)
    for item in kept:
        final_by_bucket[item.resolve_bucket()] += item.estimated_tokens

    allocations: list[ContextBucketAllocation] = []
    for bucket, cap in policy.bucket_max_tokens.items():
        if cap <= 0:
            continue
        allocations.append(
            ContextBucketAllocation(
                bucket=bucket,  # type: ignore[arg-type]
                budget_tokens=cap,
                initial_tokens=initial_by_bucket.get(bucket, 0),
                final_tokens=final_by_bucket.get(bucket, 0),
            )
        )

    ordered: list[ContextItem] = []
    for bucket in _BUCKET_RENDER_ORDER:
        ordered.extend([i for i in kept if i.resolve_bucket() == bucket])
    ordered.extend([i for i in kept if i not in ordered])

    rendered = _items_to_messages(ordered)
    final_tokens = sum_item_tokens(kept)

    trace_dict = trace.to_dict()
    if state:
        from app.services.evidence_fidelity import compute_evidence_fidelity

        fidelity = compute_evidence_fidelity(state, kept)
        trace_dict["evidence_fidelity"] = fidelity

    composition = build_prompt_composition_view(
        purpose=purpose,
        bucket_allocations=[
            {
                "bucket": a.bucket,
                "budget_tokens": a.budget_tokens,
                "initial_tokens": a.initial_tokens,
                "final_tokens": a.final_tokens,
            }
            for a in allocations
        ],
        items_kept=kept,
        items_compressed=compressed,
        items_dropped=dropped,
        rendered_messages=rendered,
        trace=trace_dict,
    )

    if final_tokens > token_budget_total:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_context_overflow_prevented(purpose)

    return ContextEnvelope(
        purpose=purpose,
        model_name=model_name,
        token_budget_total=token_budget_total,
        bucket_allocations=allocations,
        items_kept=kept,
        items_compressed=compressed,
        items_dropped=dropped,
        rendered_messages=rendered,
        trace={**trace_dict, "composition_view": composition},
    )
