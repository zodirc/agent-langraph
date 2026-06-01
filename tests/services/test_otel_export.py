from app.runtime.state import create_initial_state
from app.services.engineering_trace import init_trace_context, record_node_span
from app.services.otel_export import export_engineering_spans, finalize_trace_export


def test_export_spans_json_backend():
    state = create_initial_state(task_id="t1")
    state = init_trace_context(state, thread_id="th-1", tenant_id="tenant-1")
    state = record_node_span(state, "planning", status="ok")
    meta = export_engineering_spans(state)
    assert meta["exported"] == 1
    assert meta["backend"] in ("json_log", "opentelemetry_console")


def test_finalize_trace_export_attaches_payload():
    state = create_initial_state(task_id="t2")
    state = init_trace_context(state)
    state = record_node_span(state, "reasoning", status="ok")
    final = finalize_trace_export(state)
    assert (final.get("input_payload") or {}).get("trace_export")
