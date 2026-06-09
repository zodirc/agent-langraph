"""Tests for stream event resume SSE formatting."""

from __future__ import annotations

from app.services.stream_event_resume import _event_to_sse, list_events_json


def test_event_to_sse_answer_delta():
    chunk = _event_to_sse(
        "t1",
        {
            "event_type": "answer_delta",
            "delta": "Hi",
            "seq": 3,
            "meta": {"node": "reasoning", "phase": "llm"},
        },
    )
    assert "event: answer_delta" in chunk
    assert '"text": "Hi"' in chunk
    assert '"seq": 3' in chunk


def test_list_events_json_empty(isolated_stores):
    out = list_events_json("missing-task", after_seq=0)
    assert out["events"] == []
    assert out["last_event_seq"] == 0
