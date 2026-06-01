from app.runtime.state import create_initial_state
from app.services.engineering_trace import (
    init_trace_context,
    record_node_span,
    record_tool_span,
    trace_summary,
)


def test_init_trace_context():
    state = create_initial_state(task_id="task-1", input_payload={"goal": "test"})
    state = init_trace_context(state, thread_id="thread-1", tenant_id="tenant-a")
    ctx = state.get("trace_context") or {}
    assert ctx["task_id"] == "task-1"
    assert ctx["thread_id"] == "thread-1"
    assert ctx["tenant_id"] == "tenant-a"
    assert ctx.get("trace_id")


def test_record_node_and_tool_spans():
    state = create_initial_state(task_id="task-2")
    state = init_trace_context(state, thread_id="thread-2")
    state = record_node_span(state, "planning", status="ok")
    state = record_tool_span(state, "echo", status="ok")
    summary = trace_summary(state)
    assert summary["span_count"] == 2
    assert summary["trace_context"]["task_id"] == "task-2"
