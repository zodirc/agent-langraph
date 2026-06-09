"""Carry forward prior revision scope for continuation turns (§13.3)."""

from __future__ import annotations

import re
from typing import Any

_CONTINUATION_RE = re.compile(
    r"(上一版|这版|还不错|再收|微调|好一点|稍微|收一点|润色一下|based on this)",
    re.IGNORECASE,
)


def is_revision_continuation_goal(goal: str) -> bool:
    return bool(_CONTINUATION_RE.search((goal or "").strip()))


def prior_revision_intent(state: dict[str, Any]) -> dict[str, Any] | None:
    payload = state.get("input_payload") or {}
    raw = payload.get("last_revision_intent")
    if isinstance(raw, dict) and raw.get("artifact_filename"):
        return dict(raw)
    progress = state.get("progress") or {}
    raw_progress = progress.get("last_revision_intent")
    if isinstance(raw_progress, dict) and raw_progress.get("artifact_filename"):
        return dict(raw_progress)
    return None


def merge_continuation_revision_intent(
    state: dict[str, Any],
    goal: str,
    revision_intent: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Inherit artifact/sections from last revision when user continues micro-editing."""
    if not revision_intent or not is_revision_continuation_goal(goal):
        return revision_intent
    prior = prior_revision_intent(state)
    if not prior:
        return revision_intent
    merged = {**prior, **revision_intent}
    if not merged.get("target_sections") and prior.get("target_sections"):
        merged["target_sections"] = list(prior["target_sections"])
    if not merged.get("artifact_filename"):
        merged["artifact_filename"] = prior.get("artifact_filename")
    if not merged.get("artifact_role"):
        merged["artifact_role"] = prior.get("artifact_role")
    merged["source"] = "continuation"
    merged["confidence"] = max(float(merged.get("confidence") or 0), 0.75)
    return merged


def store_last_revision_intent(payload: dict[str, Any], revision_intent: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["last_revision_intent"] = dict(revision_intent)
    return out
