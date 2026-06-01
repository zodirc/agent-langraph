"""
OpenTelemetry-compatible span export for engineering_trace spans.

Uses OTLP when opentelemetry SDK is installed; otherwise logs JSON summary.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.runtime.state import AgentState

logger = logging.getLogger(__name__)


def export_engineering_spans(state: AgentState) -> dict[str, Any]:
    """Export spans from state; return export metadata."""
    spans = list(state.get("engineering_spans") or [])
    ctx = dict(state.get("trace_context") or {})
    if not spans:
        return {"exported": 0, "backend": "none"}

    backend = "json_log"
    exported = 0

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        resource = Resource.create(
            {
                "service.name": "agent-langraph",
                "task.id": ctx.get("task_id"),
                "tenant.id": ctx.get("tenant_id"),
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        tracer = trace.get_tracer("agent-langraph")

        for raw in spans:
            with tracer.start_as_current_span(
                str(raw.get("name") or "span"),
                attributes=_span_attributes(raw, ctx),
            ):
                exported += 1
        backend = "opentelemetry_console"
    except ImportError:
        logger.info(
            "engineering_trace_export %s",
            json.dumps({"trace_context": ctx, "spans": spans}, ensure_ascii=False)[:4000],
        )
        exported = len(spans)

    return {"exported": exported, "backend": backend, "trace_id": ctx.get("trace_id")}


def _span_attributes(span: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    attrs = dict(span.get("attributes") or {})
    attrs.update(
        {
            "span.kind": span.get("kind"),
            "span.status": span.get("status"),
            "trace.thread_id": ctx.get("thread_id"),
            "trace.request_id": ctx.get("request_id"),
            "trace.graph_version": ctx.get("graph_version"),
        }
    )
    if span.get("error"):
        attrs["error.message"] = str(span.get("error"))[:500]
    return {k: v for k, v in attrs.items() if v is not None}


def finalize_trace_export(state: AgentState) -> AgentState:
    """Attach export metadata to state at turn boundary."""
    from app.runtime.state import merge_state

    meta = export_engineering_spans(state)
    payload = dict(state.get("input_payload") or {})
    payload["trace_export"] = meta
    return merge_state(state, input_payload=payload)
