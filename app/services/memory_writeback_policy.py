"""Config-driven gates for long-term memory writeback."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.runtime.state import AgentState, TaskStatus


def _writeback_cfg() -> dict[str, Any]:
    raw = getattr(settings, "MEMORY_WRITEBACK_CONFIG", None)
    return raw if isinstance(raw, dict) else {}


def should_index_episode(state: AgentState | dict[str, Any]) -> bool:
    """Return False to skip structured episode indexing for this turn."""
    cfg = _writeback_cfg()
    skip_when = cfg.get("skip_when") or []
    if not isinstance(skip_when, list):
        skip_when = []

    status = str(state.get("status") or "")
    for rule in skip_when:
        if not isinstance(rule, dict):
            continue
        if rule.get("status") and status == str(rule["status"]):
            return False

    reasoning = state.get("reasoning_result") or {}
    structured = reasoning.get("structured") if isinstance(reasoning.get("structured"), dict) else {}
    if structured.get("code_verify_failed") is True:
        return False
    for rule in skip_when:
        if not isinstance(rule, dict):
            continue
        key = rule.get("structured_key")
        if key and structured.get(key):
            return False

    index_kinds = cfg.get("index_kinds")
    if isinstance(index_kinds, list) and index_kinds:
        payload = state.get("input_payload") or {}
        audit = payload.get("route_audit") or {}
        kind = str(audit.get("inferred_kind") or "general")
        if kind not in [str(k) for k in index_kinds]:
            return False

    return True
