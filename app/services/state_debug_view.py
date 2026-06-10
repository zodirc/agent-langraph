"""Prepare AgentState snapshots for Web CLI debug UI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.runtime.state import AgentState, merge_state
from app.services.live_task_state import LiveTaskEntry

# Max list items kept per key when truncate=True.
_LIST_ITEM_LIMITS: dict[str, int] = {
    "conversation_history": 24,
    "retrieved_knowledge": 12,
    "tool_results": 16,
    "memory_hits": 16,
    "observations": 24,
    "audit_log": 80,
    "node_history": 120,
    "engineering_spans": 40,
    "errors": 40,
    "artifacts": 24,
}

# Max string length for large prose fields.
_STRING_CHAR_LIMITS: dict[str, int] = {
    "final_answer": 12000,
    "streaming_answer_text": 12000,
}


def _truncate_list(key: str, items: list[Any]) -> tuple[list[Any], bool]:
    limit = _LIST_ITEM_LIMITS.get(key)
    if limit is None or len(items) <= limit:
        return items, False
    return list(items[-limit:]), True


def _truncate_string(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    omitted = len(value) - limit
    return (
        value[:limit] + f"\n… [{omitted} chars truncated for debug view]",
        True,
    )


def _truncate_nested(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    if depth >= max_depth:
        if isinstance(value, (dict, list)):
            return {"_truncated": True, "_reason": "max_depth"}
        return value
    if isinstance(value, dict):
        return {k: _truncate_nested(v, depth=depth + 1, max_depth=max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 200:
            tail = [_truncate_nested(v, depth=depth + 1, max_depth=max_depth) for v in value[-200:]]
            return {"_truncated": True, "_original_length": len(value), "items": tail}
        return [_truncate_nested(v, depth=depth + 1, max_depth=max_depth) for v in value]
    if isinstance(value, str) and len(value) > 16000:
        return _truncate_string(value, 16000)[0]
    return value


def build_state_debug_view(
    state: AgentState,
    *,
    truncate: bool = True,
) -> dict[str, Any]:
    """
    Return a JSON-serializable AgentState for the debug UI.

    When truncate=True, long lists/strings are clipped; response metadata lists what was clipped.
    """
    raw = dict(state)
    truncated_fields: list[str] = []

    if truncate:
        for key, limit in _STRING_CHAR_LIMITS.items():
            val = raw.get(key)
            if isinstance(val, str):
                clipped, did = _truncate_string(val, limit)
                if did:
                    raw[key] = clipped
                    truncated_fields.append(key)

        for key in _LIST_ITEM_LIMITS:
            val = raw.get(key)
            if isinstance(val, list):
                clipped, did = _truncate_list(key, val)
                if did:
                    raw[key] = clipped
                    truncated_fields.append(f"{key}[{len(val)}→{len(clipped)}]")

        for key in (
            "input_payload",
            "mission_control",
            "turn_facts",
            "turn_event_log",
            "trace_context",
            "structured_output",
            "reasoning_result",
            "reflection_result",
        ):
            val = raw.get(key)
            if val is not None:
                raw[key] = _truncate_nested(val)

    field_summary = {
        key: _describe_value(raw.get(key))
        for key in sorted(raw.keys())
    }

    trace_ctx = raw.get("trace_context") if isinstance(raw.get("trace_context"), dict) else {}
    context_composition = trace_ctx.get("last_context_composition")

    return {
        "task_id": str(raw.get("task_id", "")),
        "session_id": str(raw.get("session_id", "")),
        "status": str(raw.get("status", "")),
        "current_node": str(raw.get("current_node", "")),
        "execution_mode": str(raw.get("execution_mode") or "single"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "truncated": bool(truncated_fields),
        "truncated_fields": truncated_fields,
        "field_summary": field_summary,
        "context_composition": context_composition,
        "state": raw,
    }


def build_merged_debug_state(
    store_state: AgentState,
    live_state: AgentState,
) -> AgentState:
    """Overlay live in-flight state on persisted store (nested dicts deep-merge)."""
    return merge_state(store_state, **dict(live_state))


def build_task_state_debug_response(
    *,
    task_id: str,
    store_state: Optional[AgentState],
    live_entry: Optional[LiveTaskEntry],
    truncate: bool = True,
) -> dict[str, Any]:
    """
    Debug payload with separate store / live / merged views for Web CLI.

    store_state may be None when the task exists only in-memory (rare).
    """
    store_view: Optional[dict[str, Any]] = None
    live_view: Optional[dict[str, Any]] = None
    merged_view: Optional[dict[str, Any]] = None

    if store_state is not None:
        store_view = build_state_debug_view(store_state, truncate=truncate)

    if live_entry is not None:
        live_view = build_state_debug_view(live_entry.state, truncate=truncate)
        live_view["live_running"] = live_entry.running
        live_view["live_updated_at"] = live_entry.updated_at

    if store_state is not None and live_entry is not None:
        merged = build_merged_debug_state(store_state, live_entry.state)
        merged_view = build_state_debug_view(merged, truncate=truncate)
        merged_view["merge_note"] = "store + live overlay (nested dicts merged)"
    elif live_entry is not None:
        merged_view = live_view
    elif store_state is not None:
        merged_view = store_view

    primary = merged_view or store_view or live_view or {}
    return {
        "task_id": task_id,
        "session_id": str(
            (store_state or (live_entry.state if live_entry else {})).get("session_id", task_id)
        ),
        "live_available": live_entry is not None,
        "live_running": bool(live_entry and live_entry.running),
        "live_updated_at": live_entry.updated_at if live_entry else None,
        "store_available": store_state is not None,
        "sources": {
            "store": store_view,
            "live": live_view,
            "merged": merged_view,
        },
        # Back-compat: default inspector view = merged when live exists.
        **{k: v for k, v in primary.items() if k != "merge_note"},
        "state": primary.get("state", {}),
        "field_summary": primary.get("field_summary", {}),
        "status": primary.get("status", ""),
        "current_node": primary.get("current_node", ""),
        "execution_mode": primary.get("execution_mode", "single"),
        "fetched_at": primary.get("fetched_at", ""),
        "truncated": primary.get("truncated", False),
        "truncated_fields": primary.get("truncated_fields", []),
    }


def _describe_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    if isinstance(value, str):
        return f"str({len(value)})"
    if isinstance(value, list):
        return f"list({len(value)})"
    if isinstance(value, dict):
        return f"dict({len(value)})"
    return type(value).__name__
