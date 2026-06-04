"""
Post-write delivery policy (Cursor/Copilot-style): disk is source of truth.

After a successful write_text_artifact / append, skip slow reasoning re-generation
and surface the actual path + bytes to the user.
"""

from __future__ import annotations

from typing import Any

_WRITE_TOOLS = frozenset({"write_text_artifact", "append_text_artifact"})


def writing_tool_results_ok(tool_results: list[Any] | None) -> bool:
    for item in tool_results or []:
        if str(item.get("tool") or "") not in _WRITE_TOOLS:
            continue
        if str(item.get("status") or "ok").lower() not in ("ok", "success"):
            continue
        res = item.get("result") if isinstance(item.get("result"), dict) else {}
        if int(res.get("bytes") or 0) > 0:
            return True
    return False


def manuscript_has_fresh_write(manuscript: dict[str, Any] | None) -> bool:
    ms = manuscript if isinstance(manuscript, dict) else {}
    return int(ms.get("outline_bytes") or 0) > 0 or int(ms.get("body_bytes") or 0) > 0


def writing_persisted_on_state(state: dict[str, Any]) -> bool:
    return writing_tool_results_ok(state.get("tool_results")) or manuscript_has_fresh_write(
        state.get("manuscript")
    )


def clear_reasoning_regen_after_persist(payload: dict[str, Any]) -> dict[str, Any]:
    """Prefer deterministic post-write summary over full reasoning LLM."""
    out = dict(payload)
    out.pop("force_slow_reasoning", None)
    out["skip_reasoning_after_tools"] = True
    audit = dict(out.get("route_audit") or {})
    audit.pop("force_slow_reasoning", None)
    out["route_audit"] = audit
    return out


def should_force_slow_reasoning_after_write(
    payload: dict[str, Any],
    audit: dict[str, Any],
    *,
    tool_results: list[Any] | None,
    revision_intent: bool = False,
) -> bool:
    if revision_intent:
        return True
    if writing_tool_results_ok(tool_results):
        return False
    return bool(
        payload.get("force_slow_reasoning")
        or audit.get("force_slow_reasoning")
    )
