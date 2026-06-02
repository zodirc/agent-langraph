"""Writing stream SSE helpers."""

import app.services.stream_progress as sp
from app.services.writing_stream import (
    emit_full_content_deltas,
    emit_writing_content_deltas,
    maybe_report_writing_buffer_trace,
    writing_stream_enabled,
)


def test_writing_stream_enabled_default(test_settings, monkeypatch):
    import app.services.writing_stream as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.WRITING_STREAM_ENABLED = True
    assert writing_stream_enabled() is True


def test_emit_writing_content_deltas(monkeypatch, test_settings):
    import app.services.writing_stream as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.WRITING_STREAM_ENABLED = True
    events: list[dict] = []
    monkeypatch.setattr(sp, "_writing_handler", lambda e: events.append(e))

    raw = '{"content": "第一章正文开始"}'
    seen = 0
    for i in range(4, len(raw) + 1, 4):
        seen = emit_writing_content_deltas(
            raw[:i],
            seen,
            filename="novel.txt",
            min_delta=1,
        )
    assert seen > 0
    assert events
    assert "第一章" in "".join(e["text"] for e in events)
    assert events[0]["filename"] == "novel.txt"


def test_maybe_report_writing_buffer_trace_throttled(monkeypatch, test_settings):
    import app.services.writing_stream as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    traces: list[str] = []

    def _capture(node: str, message: str) -> None:
        traces.append(f"{node}:{message}")

    monkeypatch.setattr(mod, "trace_enabled", lambda: True)
    monkeypatch.setattr(mod, "report_status_trace", _capture)

    t0 = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: t0)
    assert (
        maybe_report_writing_buffer_trace(
            filename="novel.txt",
            accumulated_len=200,
            content_seen_len=0,
            last_report_at=0.0,
        )
        == t0
    )
    assert len(traces) == 1
    assert "novel.txt" in traces[0]

    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 2.0)
    assert (
        maybe_report_writing_buffer_trace(
            filename="novel.txt",
            accumulated_len=400,
            content_seen_len=0,
            last_report_at=t0,
        )
        == t0
    )
    assert len(traces) == 1

    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 6.0)
    assert (
        maybe_report_writing_buffer_trace(
            filename="novel.txt",
            accumulated_len=800,
            content_seen_len=0,
            last_report_at=t0,
        )
        == t0 + 6.0
    )
    assert len(traces) == 2

    assert (
        maybe_report_writing_buffer_trace(
            filename="novel.txt",
            accumulated_len=900,
            content_seen_len=12,
            last_report_at=t0 + 6.0,
        )
        == t0 + 6.0
    )
    assert len(traces) == 2


def test_emit_writing_content_deltas_unclosed_string(monkeypatch, test_settings):
    import app.services.writing_stream as mod
    from app.services.artifact_args_parser import ArtifactArgsParser

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.WRITING_STREAM_ENABLED = True
    events: list[dict] = []
    monkeypatch.setattr(sp, "_writing_handler", lambda e: events.append(e))

    parser = ArtifactArgsParser()
    raw = '{"content": "流式未闭合'
    parser.feed(raw)
    seen = emit_writing_content_deltas(
        raw,
        0,
        filename="novel.txt",
        min_delta=1,
        parser=parser,
    )
    assert seen == len("流式未闭合")
    assert events
    assert "流式" in events[0]["text"]


def test_emit_full_content_deltas(monkeypatch, test_settings):
    import app.services.writing_stream as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.WRITING_STREAM_ENABLED = True
    events: list[dict] = []
    monkeypatch.setattr(sp, "_writing_handler", lambda e: events.append(e))

    emit_full_content_deltas("outline.txt", "大纲内容" * 50, chunk_size=40)
    assert events
    assert events[0].get("reset") is True
    assert "完成 outline.txt" in events[-1]["text"]
