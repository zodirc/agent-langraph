"""Turn lifecycle: delivered event, quiet post-delivery nodes, async close."""

import json
import threading
import time

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.graph_runner import (
    GraphRunner,
    _format_delivered_event,
    _should_emit_node_event,
)
from app.services.close_turn_async import _close_turn_sync


def test_should_emit_node_event_hides_post_delivery_nodes():
    state = create_initial_state()
    assert _should_emit_node_event("output", state) is True
    assert _should_emit_node_event("memory_writeback", state) is False
    assert _should_emit_node_event("eval_capture", state) is False


def test_format_delivered_event_payload():
    state = merge_state(
        create_initial_state(task_id="del-1"),
        status=TaskStatus.COMPLETED.value,
        final_answer="hello world",
        structured_output={"tool_count": 0},
        session_turn=2,
    )
    raw = _format_delivered_event(state)
    assert "event: delivered" in raw
    data_line = [ln for ln in raw.splitlines() if ln.startswith("data: ")][0]
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["task_id"] == "del-1"
    assert payload["final_answer"] == "hello world"
    assert payload["session_turn"] == 2


def test_close_turn_sync_records_eval_and_turn_closed(isolated_stores, monkeypatch):
    monkeypatch.setattr("app.config.settings.settings.SESSION_ENABLED", True)
    state = merge_state(
        create_initial_state(task_id="close-1"),
        status=TaskStatus.COMPLETED.value,
        final_answer="done",
        session_turn=1,
        input_payload={"goal": "test"},
    )
    out = _close_turn_sync(state)
    bg = out.get("background_status") or {}
    assert bg.get("turn_closed") is True
    assert (out.get("eval_capture") or {}).get("eligible") is True
    audit_nodes = [e.get("node") for e in out.get("audit_log") or []]
    assert "turn_closed" in audit_nodes


def test_stream_single_emits_delivered_before_done(isolated_stores, monkeypatch):
    """delivered fires at output; heartbeat suppressed after; done still arrives."""
    events: list[str] = []

    def fake_stream_graph(state, *, thread_id=None):
        yield "output", merge_state(
            state,
            status=TaskStatus.COMPLETED.value,
            final_answer="streamed answer",
            current_node="output",
        )

    monkeypatch.setattr("app.services.graph_runner.stream_graph", fake_stream_graph)
    monkeypatch.setattr(
        "app.services.graph_runner.answer_stream_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.graph_runner.trace_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.graph_runner.thinking_stream_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.graph_runner.writing_stream_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.close_turn_async.close_turn_async",
        lambda _state: None,
    )

    state = merge_state(
        create_initial_state(task_id="stream-delivered-1"),
        session_id="stream-delivered-1",
        session_turn=1,
        input_payload={"goal": "hi"},
    )

    runner = GraphRunner()
    for chunk in runner._stream_single(state, created=True):
        events.append(chunk)

    body = "".join(events)
    assert "event: delivered" in body
    assert "event: done" in body
    delivered_idx = body.index("event: delivered")
    done_idx = body.index("event: done")
    assert delivered_idx < done_idx
    assert body.count("仍在处理中") == 0
