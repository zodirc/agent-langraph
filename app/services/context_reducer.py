"""Dedupe, prune, compress context items under token budget (ADR §7)."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.config.settings import settings
from app.services.context_estimator import estimate_item_tokens, estimate_text_tokens, sum_item_tokens
from app.services.context_fingerprint import dedupe_context_items
from app.services.context_items import ContextBucketName, ContextItem
from app.services.context_policy import PromptContextPolicy
from app.services.context_trace import ContextAssemblyTrace
from app.services.conversation_context import compress_conversation_history
from app.services.context_compressor import apply_semantic_context_compress


_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _record_quality_regression(purpose: str) -> None:
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_context_quality_regression(purpose)
    except Exception:
        pass


def _sort_items(items: list[ContextItem]) -> list[ContextItem]:
    return sorted(
        items,
        key=lambda i: (_PRIORITY_RANK.get(i.priority, 9), -i.freshness, i.id),
    )


def dedupe_items(items: list[ContextItem]) -> list[ContextItem]:
    return dedupe_context_items(items)


def _compress_transcript_items(
    items: list[ContextItem],
    *,
    state: dict[str, Any] | None,
    trace: ContextAssemblyTrace,
) -> list[ContextItem]:
    if not items:
        return items
    messages = [
        {"role": i.role, "content": i.content, "at": i.meta.get("at")}
        for i in items
    ]
    if settings.CONTEXT_COMPRESS_SEMANTIC_ENABLED:
        compressed = apply_semantic_context_compress(messages, state=state)
        method = "semantic"
    else:
        compressed = compress_conversation_history(messages)
        method = "character_failsafe"
    out: list[ContextItem] = []
    for msg in compressed:
        item = ContextItem.from_message(
            msg,
            kind="recent_history" if msg.get("role") != "system" else "semantic_summary",
            source="session",
            priority="medium" if msg.get("role") != "system" else "high",
            compressible=True,
            droppable=False,
        )
        item = estimate_item_tokens(item)
        if msg.get("at") == "semantic_compress" or "[Semantic context summary]" in str(
            msg.get("content", "")
        ):
            item.kind = "semantic_summary"
            item.bucket = "semantic_summary"
            item.priority = "high"
        trace.record_compressed(
            items[0] if items else item,
            reason="transcript_bucket_over_budget",
            tokens_after=item.estimated_tokens,
            method=method,
        )
        out.append(item)
    return out


def _clip_content(item: ContextItem, clipped: str, *, method: str) -> ContextItem:
    return ContextItem(
        id=item.id,
        kind=item.kind,
        source=item.source,
        role=item.role,
        content=clipped,
        priority=item.priority,
        freshness=item.freshness,
        estimated_tokens=estimate_text_tokens(clipped),
        compressible=item.compressible,
        droppable=item.droppable,
        bucket=item.bucket,
        meta={**item.meta, "truncated": True, "compress_method": method},
    )


def _compress_memory_item(item: ContextItem, max_tokens: int) -> ContextItem:
    text = item.content or ""
    for marker in ("Result:", "Decision:", "Outcome:", "Episode:"):
        idx = text.find(marker)
        if idx >= 0:
            snippet = text[idx : idx + max(80, max_tokens * 4)]
            if len(snippet) < len(text):
                return _clip_content(item, snippet.rstrip() + "…", method="memory_structured")
    return _clip_content(item, text[: max(80, max_tokens * 4)].rstrip() + "…", method="memory_tail")


def _compress_knowledge_item(item: ContextItem, max_tokens: int) -> ContextItem:
    text = item.content or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return _clip_content(item, text[: max(80, max_tokens * 4)], method="knowledge_tail")
    kept: list[str] = []
    budget = max(80, max_tokens * 4)
    for line in lines:
        if line.lower().startswith(("conclusion", "summary", "result", "finding")):
            kept.insert(0, line)
        elif len("\n".join(kept)) + len(line) + 1 <= budget:
            kept.append(line)
        if len("\n".join(kept)) >= budget:
            break
    if not kept:
        kept = lines[:3]
    clipped = "\n".join(kept[:6])
    if len(clipped) > budget:
        clipped = clipped[:budget] + "…"
    return _clip_content(item, clipped, method="knowledge_structured")


def _compress_tool_item(item: ContextItem, max_tokens: int) -> ContextItem:
    text = item.content or ""
    match = re.match(r"^\s*\[([^\]]+)\]\s*([^:]+):\s*(.*)$", text, re.DOTALL)
    if match:
        name, status, body = match.groups()
        body_budget = max(40, max_tokens * 3)
        body = (body or "").strip()
        if len(body) > body_budget:
            body = body[:body_budget] + "…"
        clipped = f"[{name}] {status.strip()}: {body}"
        return _clip_content(item, clipped, method="tool_structured")
    return _clip_content(item, text[: max(80, max_tokens * 4)].rstrip() + "…", method="tool_tail")


def _compress_diagnostic_item(item: ContextItem, max_tokens: int) -> ContextItem:
    text = item.content or ""
    hints: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if any(k in lower for k in ("error", "failed", "exception", "line", "file", "cause", "root")):
            hints.append(line.strip())
    clipped = "\n".join(hints[:4]) if hints else text[: max(80, max_tokens * 4)]
    if len(clipped) > max(80, max_tokens * 4):
        clipped = clipped[: max(80, max_tokens * 4)] + "…"
    return _clip_content(item, clipped, method="diagnostic_structured")


def _truncate_item_content(item: ContextItem, max_tokens: int) -> ContextItem:
    if item.estimated_tokens <= max_tokens:
        return item
    bucket = item.resolve_bucket()
    if bucket == "retrieved_memory" or item.kind == "episodic_memory":
        return _compress_memory_item(item, max_tokens)
    if bucket == "retrieved_knowledge" or item.kind == "knowledge":
        return _compress_knowledge_item(item, max_tokens)
    if bucket == "tool_observations" or item.kind == "tool_output":
        return _compress_tool_item(item, max_tokens)
    if bucket == "diagnostics" or item.kind in ("diagnostic", "test_failure", "terminal_output"):
        return _compress_diagnostic_item(item, max_tokens)
    char_budget = max(80, max_tokens * 4)
    clipped = (item.content or "")[:char_budget]
    if len(item.content or "") > len(clipped):
        clipped += "…"
    return _clip_content(item, clipped, method="token_truncate")


def _apply_min_bucket_representation(
    bucket_items: list[ContextItem],
    *,
    bucket: ContextBucketName,
    policy: PromptContextPolicy,
) -> list[ContextItem]:
    """Keep a minimal required representation instead of the full bucket."""
    min_tokens = policy.min_bucket_tokens.get(bucket)
    min_items = policy.minimum_required_items.get(bucket, 1)
    if not min_tokens or not bucket_items:
        return bucket_items
    sorted_items = _sort_items(bucket_items)
    keep = sorted_items[: max(1, min_items)]
    out: list[ContextItem] = []
    per_item = max(32, min_tokens // max(1, len(keep)))
    for item in keep:
        if policy.allow_summary_substitute.get(bucket) and item.kind in (
            "working_memory",
            "semantic_summary",
        ):
            lines = [ln for ln in (item.content or "").splitlines() if ln.strip()][:4]
            summary = "\n".join(lines)
            out.append(_clip_content(item, summary, method="min_bucket_summary"))
        else:
            out.append(_truncate_item_content(item, per_item))
    return out


def reduce_context_items(
    items: list[ContextItem],
    policy: PromptContextPolicy,
    *,
    token_budget_total: int,
    state: dict[str, Any] | None = None,
    trace: ContextAssemblyTrace | None = None,
) -> tuple[list[ContextItem], list[ContextItem], list[ContextItem]]:
    """
    Return (kept, compressed, dropped) after bucket caps and global budget.
    """
    assembly_trace = trace or ContextAssemblyTrace(purpose=policy.purpose)
    items = dedupe_items([estimate_item_tokens(i) for i in items])

    by_bucket: dict[ContextBucketName, list[ContextItem]] = defaultdict(list)
    for item in items:
        by_bucket[item.resolve_bucket()].append(item)

    kept: list[ContextItem] = []
    compressed: list[ContextItem] = []
    dropped: list[ContextItem] = []

    for bucket, bucket_items in by_bucket.items():
        cap = policy.bucket_max_tokens.get(bucket, 8000)
        bucket_items = _sort_items(bucket_items)
        bucket_tokens = sum_item_tokens(bucket_items)

        if bucket_tokens > cap and bucket in policy.compressible_buckets:
            if bucket == "recent_transcript":
                merged = _compress_transcript_items(bucket_items, state=state, trace=assembly_trace)
                for old in bucket_items:
                    compressed.append(old)
                bucket_items = merged
                bucket_tokens = sum_item_tokens(bucket_items)

        if (
            bucket_tokens > cap
            and bucket in policy.required_buckets
            and bucket in policy.min_bucket_tokens
        ):
            minimized = _apply_min_bucket_representation(
                bucket_items, bucket=bucket, policy=policy
            )
            if minimized and sum_item_tokens(minimized) < bucket_tokens:
                for old in bucket_items:
                    if old not in minimized:
                        compressed.append(old)
                        assembly_trace.record_compressed(
                            old,
                            reason=f"min_bucket:{bucket}",
                            tokens_after=0,
                            method="min_bucket_summary",
                        )
                bucket_items = minimized
                bucket_tokens = sum_item_tokens(bucket_items)

        while bucket_tokens > cap and bucket_items:
            if bucket in policy.droppable_buckets:
                victim = bucket_items.pop()
                if bucket in policy.required_buckets and not bucket_items:
                    bucket_items.append(victim)
                    break
                dropped.append(victim)
                assembly_trace.record_dropped(victim, reason=f"bucket_cap:{bucket}")
                if victim.priority == "critical":
                    _record_quality_regression(assembly_trace.purpose)
                bucket_tokens = sum_item_tokens(bucket_items)
                continue
            if bucket in policy.compressible_buckets and bucket != "recent_transcript":
                victim = bucket_items[-1]
                trimmed = _truncate_item_content(victim, max(32, cap // max(1, len(bucket_items))))
                compressed.append(victim)
                bucket_items[-1] = trimmed
                assembly_trace.record_compressed(
                    victim,
                    reason=f"bucket_cap_truncate:{bucket}",
                    tokens_after=trimmed.estimated_tokens,
                    method="token_truncate",
                )
                bucket_tokens = sum_item_tokens(bucket_items)
                continue
            victim = bucket_items.pop() if bucket not in policy.required_buckets else None
            if victim is None:
                victim = bucket_items.pop()
            dropped.append(victim)
            assembly_trace.record_dropped(victim, reason=f"bucket_cap_hard:{bucket}")
            bucket_tokens = sum_item_tokens(bucket_items)

        for item in bucket_items:
            kept.append(item)
            assembly_trace.record_kept(item)

    total = sum_item_tokens(kept)
    if total <= token_budget_total:
        return kept, compressed, dropped

    for bucket in policy.degrade_order:
        if total <= token_budget_total:
            break
        bucket_kept = [i for i in kept if i.resolve_bucket() == bucket]
        if not bucket_kept:
            continue
        if bucket in policy.compressible_buckets and bucket == "recent_transcript":
            new_items = _compress_transcript_items(bucket_kept, state=state, trace=assembly_trace)
            for old in bucket_kept:
                kept.remove(old)
                compressed.append(old)
            kept.extend(new_items)
            total = sum_item_tokens(kept)
            continue
        for item in sorted(bucket_kept, key=lambda i: _PRIORITY_RANK.get(i.priority, 9), reverse=True):
            if total <= token_budget_total:
                break
            if not item.droppable or bucket in policy.preserve_fidelity_buckets:
                if item.compressible:
                    trimmed = _truncate_item_content(item, max(32, item.estimated_tokens // 2))
                    compressed.append(item)
                    kept.remove(item)
                    kept.append(trimmed)
                    total = sum_item_tokens(kept)
                continue
            kept.remove(item)
            dropped.append(item)
            assembly_trace.record_dropped(item, reason=f"global_budget:{bucket}")
            if item.priority == "critical":
                _record_quality_regression(assembly_trace.purpose)
            total = sum_item_tokens(kept)

    if total > token_budget_total:
        for item in sorted(kept, key=lambda i: _PRIORITY_RANK.get(i.priority, 9), reverse=True):
            if total <= token_budget_total:
                break
            if item.priority == "critical" or not item.droppable:
                continue
            kept.remove(item)
            dropped.append(item)
            assembly_trace.record_dropped(item, reason="global_budget_overflow")
            if item.priority == "critical":
                _record_quality_regression(assembly_trace.purpose)
            total = sum_item_tokens(kept)

    return kept, compressed, dropped
