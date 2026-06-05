"""Context fingerprinting for fact-level dedup across sources (ADR §7)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.services.context_items import ContextItem

_DYNAMIC_ID_RE = re.compile(
    r"\b(?:ctx|mem|doc|tool|rt|rm|rk|wm|sum|out|turn)_[a-f0-9]{8,}\b",
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_TEMPLATE_PREFIX_RE = re.compile(
    r"^\s*\[(?:Working memory|Semantic context summary|Session outcomes)\]\s*",
    re.IGNORECASE,
)
_TOOL_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*([^:]+):\s*", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_content_text(text: str) -> str:
    """Strip noise so lightly rewritten facts share the same fingerprint."""
    normalized = str(text or "").strip().lower()
    normalized = _TEMPLATE_PREFIX_RE.sub("", normalized)
    normalized = _DYNAMIC_ID_RE.sub("", normalized)
    normalized = _TIMESTAMP_RE.sub("", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def _normalized_hash(text: str) -> str:
    normalized = normalize_content_text(text)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def source_aware_key(item: ContextItem | dict[str, Any]) -> str | None:
    """Stable id-based key when source metadata is available."""
    if isinstance(item, ContextItem):
        meta = item.meta or {}
        kind = item.kind
        content = item.content
    else:
        meta = dict(item.get("meta") or {})
        kind = str(item.get("kind") or "")
        content = str(item.get("content") or "")

    memory_id = meta.get("memory_id") or meta.get("episode_id")
    if memory_id:
        return f"memory:{memory_id}"

    doc_id = meta.get("doc_id") or meta.get("id")
    if doc_id and kind in ("knowledge", "episodic_memory"):
        return f"doc:{doc_id}"

    tool = meta.get("tool") or meta.get("name")
    status = meta.get("status")
    if tool and kind == "tool_output":
        result_hash = _normalized_hash(content)
        return f"tool:{tool}:{status or '?'}:{result_hash[:8]}"

    item_id = meta.get("registry_id")
    if not item_id:
        item_id = item.id if isinstance(item, ContextItem) else item.get("id")
    if item_id and str(item_id).startswith(("rm_", "rk_", "rt_")):
        return f"registry:{item_id}"

    return None


def strict_dedupe_key(item: ContextItem | dict[str, Any]) -> str:
    """Source id first, normalized content hash as fallback."""
    source_key = source_aware_key(item)
    if source_key:
        return source_key

    if isinstance(item, ContextItem):
        kind = item.kind
        content = item.content
    else:
        kind = str(item.get("kind") or "")
        content = str(item.get("content") or "")

    content_hash = _normalized_hash(content)
    if content_hash:
        return f"text:{kind}:{content_hash}"
    return f"text:{kind}:{content[:80]}"


def text_overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap on word tokens for near-duplicate detection."""
    ta = set(normalize_content_text(a).split())
    tb = set(normalize_content_text(b).split())
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 0.0


def is_near_duplicate(a: str, b: str, *, threshold: float = 0.65) -> bool:
    if not a or not b:
        return False
    norm_a = normalize_content_text(a)
    norm_b = normalize_content_text(b)
    if norm_a == norm_b:
        return True
    shorter_norm, longer_norm = (norm_a, norm_b) if len(norm_a) <= len(norm_b) else (norm_b, norm_a)
    if len(shorter_norm) >= 20 and shorter_norm in longer_norm:
        return True
    return text_overlap_ratio(a, b) >= threshold


def semantic_class(item: ContextItem) -> str:
    """Coarse fact class for cross-source folding."""
    kind = item.kind
    if kind == "user_turn":
        return "goal"
    if kind in ("working_memory", "semantic_summary"):
        return "state"
    if kind == "episodic_memory":
        return "result"
    if kind == "knowledge":
        return "file_fact"
    if kind == "tool_output":
        return "tool_outcome"
    if kind in ("diagnostic", "test_failure", "terminal_output"):
        return "risk"
    return "other"


def dedupe_context_items(
    items: list[ContextItem],
    *,
    near_duplicate_threshold: float = 0.65,
) -> list[ContextItem]:
    """Fingerprint dedup with optional near-duplicate collapse."""
    seen_strict: set[str] = set()
    kept: list[ContextItem] = []
    kept_texts: list[str] = []

    for item in items:
        key = strict_dedupe_key(item)
        if key in seen_strict:
            continue

        content = item.content or ""
        if any(is_near_duplicate(content, prev, threshold=near_duplicate_threshold) for prev in kept_texts):
            continue

        seen_strict.add(key)
        kept.append(item)
        if content:
            kept_texts.append(content)

    return kept


def registry_fingerprints(state: dict[str, Any]) -> set[str]:
    """Collect strict fingerprints already present in context registry."""
    from app.services.context_registry import _registry_from_state

    fps: set[str] = set()
    for raw in _registry_from_state(state):
        if isinstance(raw, dict):
            fps.add(strict_dedupe_key(raw))
    return fps
