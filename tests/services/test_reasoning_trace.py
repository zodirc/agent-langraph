import json

from app.services.reasoning_trace import (
    emit_field_deltas,
    extract_plan_steps,
    extract_field_text,
    is_thinking_blob,
    report_plan_trace,
    stream_llm_trace,
    trace_enabled,
    trace_verbose,
)


def test_extract_summary_from_partial_json():
    raw = '{"summary": "你好，这是'
    assert extract_field_text(raw, "summary") == "你好，这是"


def test_extract_plan_steps_incremental():
    raw = '{"plan": ["分析目标", "检索",'
    steps = extract_plan_steps(raw)
    assert steps == ["分析目标", "检索"]


def test_emit_field_deltas_grows(monkeypatch, test_settings):
    import app.services.reasoning_trace as mod
    import app.services.stream_progress as sp

    monkeypatch.setattr(mod.settings, "REASONING_TRACE_ENABLED", True)
    monkeypatch.setattr(mod.settings, "ANSWER_STREAM_ENABLED", False)
    events: list[dict] = []

    def capture(trace: dict) -> None:
        events.append(trace)

    monkeypatch.setattr(sp, "_trace_handler", capture)
    seen = emit_field_deltas('{"summary": "ab"}', "summary", 0, node="reasoning", phase="t")
    assert seen == 2
    assert events[-1]["text"] == "ab"
    assert events[-1]["level"] == "delta"


def test_emit_field_deltas_answer_stream(monkeypatch, test_settings):
    import app.services.reasoning_trace as mod
    import app.services.stream_progress as sp

    monkeypatch.setattr(mod.settings, "REASONING_TRACE_ENABLED", True)
    monkeypatch.setattr(mod.settings, "ANSWER_STREAM_ENABLED", True)
    trace_events: list[dict] = []
    answer_events: list[dict] = []

    monkeypatch.setattr(sp, "_trace_handler", lambda t: trace_events.append(t))
    monkeypatch.setattr(sp, "_answer_handler", lambda a: answer_events.append(a))
    seen = emit_field_deltas('{"summary": "你好"}', "summary", 0, node="reasoning", phase="llm")
    assert seen == 2
    assert len(answer_events) == 1
    assert answer_events[0]["text"] == "你好"
    assert not any(e.get("field") == "summary" for e in trace_events)


def test_stream_llm_trace_answer_only(monkeypatch, test_settings):
    import app.services.reasoning_trace as mod
    import app.services.stream_progress as sp

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.REASONING_TRACE_ENABLED = False
    test_settings.ANSWER_STREAM_ENABLED = True
    answer_events: list[str] = []
    sp.set_answer_handler(lambda a: answer_events.append(a["text"]))
    try:
        raw = '{"summary": "流式回答"}'
        out = mod.stream_llm_trace(
            (raw[i : i + 3] for i in range(0, len(raw), 3)),
            node="reasoning",
            phase="reasoning_llm",
            field="summary",
        )
    finally:
        sp.set_answer_handler(None)
    assert '"流式回答"' in out
    assert "".join(answer_events) == "流式回答"


def test_stream_llm_trace_with_plan_steps(monkeypatch, test_settings):
    import app.services.reasoning_trace as mod

    monkeypatch.setattr(mod.settings, "REASONING_TRACE_ENABLED", True)
    monkeypatch.setattr(mod.settings, "REASONING_TRACE_VERBOSE", True)
    events: list[dict] = []

    import app.services.stream_progress as sp

    monkeypatch.setattr(sp, "_trace_handler", lambda t: events.append(t))

    payload = json.dumps({"plan": ["step_a", "step_b"], "risk_level": "LOW"}, ensure_ascii=False)

    def chunks():
        for i in range(0, len(payload), 8):
            yield payload[i : i + 8]

    out = stream_llm_trace(chunks(), node="planning", phase="planning_llm", field="summary")
    assert '"step_b"' in out
    step_events = [e for e in events if e.get("field") == "plan_step"]
    assert len(step_events) >= 1


def test_stream_llm_trace_disabled(test_settings, monkeypatch):
    import app.services.reasoning_trace as mod

    monkeypatch.setattr(mod.settings, "REASONING_TRACE_ENABLED", False)

    def chunks():
        yield '{"summary": "x"}'

    assert stream_llm_trace(chunks(), node="r", phase="p") == '{"summary": "x"}'


def test_is_thinking_blob():
    assert is_thinking_blob('{"thinking": "secret"}')
    assert not is_thinking_blob("plain progress")


def test_trace_enabled_default(test_settings):
    assert trace_enabled() is True
    assert trace_verbose() is True


def test_thinking_stream_enabled_default(test_settings, monkeypatch):
    import app.services.reasoning_trace as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    assert mod.thinking_stream_enabled() is True


def test_report_plan_trace_meta(monkeypatch, test_settings):
    import app.services.reasoning_trace as mod
    import app.services.stream_progress as sp

    monkeypatch.setattr(mod.settings, "REASONING_TRACE_ENABLED", True)
    monkeypatch.setattr(mod.settings, "REASONING_TRACE_VERBOSE", True)
    events: list[dict] = []
    monkeypatch.setattr(sp, "_trace_handler", lambda t: events.append(t))
    report_plan_trace(["a"], ["echo"], meta={"跳过检索": True})
    assert events
    assert "跳过检索" in events[-1]["text"]
