"""
Engineering trace — structured spans with request/task/thread/tenant propagation.

Lightweight alternative to full OpenTelemetry; compatible with optional OTEL export later.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.runtime.state import AgentState, merge_state

GRAPH_VERSION = "agent/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def init_trace_context(
    state: AgentState,
    *,
    request_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> AgentState:
    """Attach trace context at task/session boundary."""
    payload = dict(state.get("input_payload") or {})
    existing_ctx = state.get("trace_context") or {}
    trace_id = str(payload.get("trace_id") or existing_ctx.get("trace_id") or _new_id("trace"))
    ctx = {
        "trace_id": trace_id,
        "request_id": str(request_id or payload.get("request_id") or state["task_id"]),
        "task_id": state["task_id"],
        "thread_id": str(thread_id or state.get("session_id") or state["task_id"]),
        "tenant_id": tenant_id or payload.get("tenant_id") or state.get("user_id"),
        "graph_version": GRAPH_VERSION,
        "started_at": _now_iso(),
    }
    payload["trace_id"] = trace_id
    payload["request_id"] = ctx["request_id"]
    spans = list(state.get("engineering_spans") or [])
    return merge_state(state, trace_context=ctx, engineering_spans=spans, input_payload=payload)


def get_trace_context(state: AgentState) -> dict[str, Any]:
    return dict(state.get("trace_context") or {})


def start_span(
    state: AgentState,
    name: str,
    *,
    kind: str = "node",
    parent_span_id: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> tuple[AgentState, str]:
    """Begin a span; returns updated state and span_id."""
    ctx = get_trace_context(state)
    span_id = _new_id("span")
    span = {
        "span_id": span_id,
        "trace_id": ctx.get("trace_id"),
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "start_time": _now_iso(),
        "end_time": None,
        "status": "running",
        "attributes": {
            **(attributes or {}),
            "task_id": ctx.get("task_id"),
            "thread_id": ctx.get("thread_id"),
            "tenant_id": ctx.get("tenant_id"),
            "graph_version": ctx.get("graph_version"),
        },
    }
    spans = list(state.get("engineering_spans") or [])
    spans.append(span)
    active = dict(state.get("trace_active_span") or {})
    active[kind] = span_id
    return merge_state(state, engineering_spans=spans, trace_active_span=active), span_id


def end_span(
    state: AgentState,
    span_id: str,
    *,
    status: str = "ok",
    error: Optional[str] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> AgentState:
    spans = list(state.get("engineering_spans") or [])
    for idx, span in enumerate(spans):
        if span.get("span_id") != span_id:
            continue
        patch = {
            **span,
            "end_time": _now_iso(),
            "status": status,
        }
        if error:
            patch["error"] = error[:500]
        if attributes:
            patch["attributes"] = {**(span.get("attributes") or {}), **attributes}
        spans[idx] = patch
        break
    return merge_state(state, engineering_spans=spans)


def record_node_span(state: AgentState, node_name: str, *, status: str, error: Optional[str] = None) -> AgentState:
    """Record a completed node span (start+end collapsed for streaming callbacks)."""
    ctx = get_trace_context(state)
    if not ctx:
        state = init_trace_context(state)
        ctx = get_trace_context(state)
    span_id = _new_id("span")
    span = {
        "span_id": span_id,
        "trace_id": ctx.get("trace_id"),
        "parent_span_id": None,
        "name": node_name,
        "kind": "node",
        "start_time": _now_iso(),
        "end_time": _now_iso(),
        "status": status,
        "attributes": {
            "task_id": ctx.get("task_id"),
            "thread_id": ctx.get("thread_id"),
            "tenant_id": ctx.get("tenant_id"),
            "graph_version": ctx.get("graph_version"),
            "agent_status": state.get("status"),
        },
    }
    if error:
        span["error"] = error[:500]
    spans = list(state.get("engineering_spans") or [])
    spans.append(span)
    return merge_state(state, engineering_spans=spans)


def record_tool_span(
    state: AgentState,
    tool_name: str,
    *,
    status: str = "ok",
    error: Optional[str] = None,
) -> AgentState:
    return record_node_span(
        merge_state(state, trace_context=get_trace_context(state) or {}),
        f"tool:{tool_name}",
        status=status,
        error=error,
    )


def record_llm_span(
    state: AgentState,
    purpose: str,
    *,
    status: str = "ok",
    tokens_used: Optional[int] = None,
    error: Optional[str] = None,
) -> AgentState:
    attrs: dict[str, Any] = {"purpose": purpose}
    if tokens_used is not None:
        attrs["tokens_used"] = tokens_used
    ctx = get_trace_context(state)
    if not ctx:
        state = init_trace_context(state)
        ctx = get_trace_context(state)
    span_id = _new_id("span")
    span = {
        "span_id": span_id,
        "trace_id": ctx.get("trace_id"),
        "parent_span_id": (state.get("trace_active_span") or {}).get("node"),
        "name": f"llm:{purpose}",
        "kind": "llm",
        "start_time": _now_iso(),
        "end_time": _now_iso(),
        "status": status,
        "attributes": {**attrs, "graph_version": ctx.get("graph_version")},
    }
    if error:
        span["error"] = error[:500]
    spans = list(state.get("engineering_spans") or [])
    spans.append(span)
    return merge_state(state, engineering_spans=spans)


def trace_summary(state: AgentState) -> dict[str, Any]:
    ctx = get_trace_context(state)
    spans = list(state.get("engineering_spans") or [])
    errors = [s for s in spans if str(s.get("status")) not in ("ok", "running")]
    return {
        "trace_context": ctx,
        "span_count": len(spans),
        "error_spans": len(errors),
        "last_span": spans[-1] if spans else None,
    }


@contextmanager
def span_scope(
    state: AgentState,
    name: str,
    *,
    kind: str = "node",
    attributes: Optional[dict[str, Any]] = None,
) -> Iterator[tuple[AgentState, str]]:
    updated, span_id = start_span(state, name, kind=kind, attributes=attributes)
    current = updated
    try:
        yield current, span_id
        current = end_span(current, span_id, status="ok")
    except Exception as exc:
        current = end_span(current, span_id, status="error", error=str(exc))
        raise
    state = current
