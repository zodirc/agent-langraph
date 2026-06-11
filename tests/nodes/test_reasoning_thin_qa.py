"""Thin QA reasoning — no silent retries, no LLM JSON repair on stream path."""

from __future__ import annotations

from app.runtime.state import TaskStatus, merge_state
from app.services.thin_execution import should_stream_thinking_for_state
from app.services.thinking_retry_signals import reasoning_result_needs_retry


def test_parser_fallback_does_not_trigger_retry():
    assert (
        reasoning_result_needs_retry(
            {"structured": {"parser_fallback": True}, "confidence": 0.45}
        )
        is False
    )


def test_thin_profile_disables_thinking_stream(base_state):
    state = merge_state(
        base_state,
        input_payload={
            **base_state["input_payload"],
            "thin_execution_profile": "qa_direct",
        },
    )
    assert should_stream_thinking_for_state(state) is False


def test_thin_qa_direct_single_llm_call_on_truncated_json(base_state, monkeypatch):
    """Truncated stream output must not trigger repair LLM or silent retry invoke."""
    from app.nodes.reasoning_node import _run_reasoning_llm_loop
    from app.services.resource_budget import budget_context_from_state, init_task_budget

    stream_calls = 0
    invoke_calls = 0
    repair_calls = 0
    thinking_deltas: list[str] = []

    def _stream_structured(*_a, **kwargs):
        nonlocal stream_calls
        stream_calls += 1
        assert kwargs.get("emit_thinking_override") is False

        def _gen():
            yield 'The user said hello. {"summary": "你好！'

        return _gen()

    def _invoke_structured(*_a, **_k):
        nonlocal invoke_calls
        invoke_calls += 1
        return {"summary": "silent retry", "confidence": 0.9, "structured": {}}

    def _repair(*_a, **_k):
        nonlocal repair_calls
        repair_calls += 1
        return None

    def _thinking_delta(**kwargs):
        thinking_deltas.append(kwargs.get("text") or "")

    monkeypatch.setattr("app.nodes.reasoning_node.stream_structured", _stream_structured)
    monkeypatch.setattr("app.nodes.reasoning_node.invoke_structured", _invoke_structured)
    monkeypatch.setattr("app.nodes.reasoning_node.stream_llm_trace", lambda chunks, **_: "".join(chunks))
    monkeypatch.setattr("app.nodes.reasoning_node.trace_enabled", lambda: True)
    monkeypatch.setattr("app.nodes.reasoning_node.answer_stream_enabled", lambda: True)
    monkeypatch.setattr(
        "app.services.thinking_retry_signals.max_thinking_retries",
        lambda: 2,
    )
    monkeypatch.setattr("app.services.llm_client._attempt_reasoning_json_repair", _repair)
    monkeypatch.setattr("app.services.stream_progress.report_thinking_delta", _thinking_delta)

    state = init_task_budget(
        merge_state(
            base_state,
            input_payload={
                **base_state["input_payload"],
                "thin_execution_profile": "qa_direct",
                "goal": "你好",
            },
        )
    )
    budget_ctx = budget_context_from_state(state)
    result, _raw, _working = _run_reasoning_llm_loop(
        state,
        context={"goal": "你好"},
        reasoning_system="thin",
        llm_purpose="routing",
        mode="direct",
        budget_ctx=budget_ctx,
    )

    assert stream_calls == 1
    assert invoke_calls == 0
    assert repair_calls == 0
    assert thinking_deltas == []
    assert result["summary"]
    assert (result.get("structured") or {}).get("parser_fallback") is True


def test_routing_profile_limits_to_one_attempt(base_state, monkeypatch):
    from app.nodes.reasoning_node import _run_reasoning_llm_loop
    from app.services.resource_budget import budget_context_from_state, init_task_budget

    calls: list[str] = []

    def _stream_structured(*_a, **_k):
        calls.append("stream")

        def _gen():
            yield '{"summary": "ok", "confidence": 0.45, "structured": {}}'

        return _gen()

    def _invoke_structured(*_a, **_k):
        calls.append("invoke")
        return {"summary": "retry", "confidence": 0.9, "structured": {}}

    monkeypatch.setattr("app.nodes.reasoning_node.stream_structured", _stream_structured)
    monkeypatch.setattr("app.nodes.reasoning_node.invoke_structured", _invoke_structured)
    monkeypatch.setattr("app.nodes.reasoning_node.stream_llm_trace", lambda chunks, **_: "".join(chunks))
    monkeypatch.setattr("app.nodes.reasoning_node.trace_enabled", lambda: True)
    monkeypatch.setattr("app.nodes.reasoning_node.answer_stream_enabled", lambda: False)
    monkeypatch.setattr(
        "app.services.thinking_retry_signals.max_thinking_retries",
        lambda: 2,
    )

    state = init_task_budget(base_state)
    budget_ctx = budget_context_from_state(state)
    _run_reasoning_llm_loop(
        state,
        context={"goal": "test"},
        reasoning_system="sys",
        llm_purpose="routing",
        mode="direct",
        budget_ctx=budget_ctx,
    )
    assert calls == ["stream"]
