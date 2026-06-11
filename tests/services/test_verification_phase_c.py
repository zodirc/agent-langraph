"""Phase C: stream guard, async reflection, planning/thinking retries."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.runtime.planning_gate_router import route_after_incremental_planning
from app.nodes.verification_node import _submission_decision, verification_node
from app.services.stream_output_guard import (
    reset_stream_output_guard,
    scan_summary_before_emit,
    consume_stream_guard_result,
)
from app.services.thinking_retry_signals import reasoning_result_needs_retry
from app.services.reflect_turn_async import reflect_turn_audit_sync
from app.services.reasoning_trace import emit_field_deltas


def test_submission_decision_ignores_reflection_retry_routes():
    state = merge_state(
        create_initial_state(),
        reflection_result={"route": "retry_reasoning"},
        status=TaskStatus.REASONED.value,
    )
    assert _submission_decision(state)["decision"] == "submit"


def test_verification_node_submit_without_reflection_result():
    out = verification_node(
        merge_state(create_initial_state(), status=TaskStatus.REASONED.value)
    )
    assert "reflection" not in (out.get("verification_result") or {})
    assert out.get("submission_decision", {}).get("decision") == "submit"


def test_stream_guard_blocks_pii_during_emit(monkeypatch):
    monkeypatch.setattr(
        "app.services.reasoning_trace.answer_stream_enabled",
        lambda: True,
    )
    reset_stream_output_guard()
    seen = emit_field_deltas(
        '{"summary": "contact me at leak@example.com"}',
        "summary",
        0,
        node="reasoning",
        phase="reasoning_llm",
        min_delta=1,
    )
    assert seen == 0
    assert consume_stream_guard_result() is not None


def test_reasoning_retry_detects_low_confidence():
    assert reasoning_result_needs_retry({"confidence": 0.4, "structured": {}}) is True
    assert reasoning_result_needs_retry({"confidence": 0.9, "structured": {}}) is False
    assert (
        reasoning_result_needs_retry(
            {"confidence": 0.45, "structured": {"parser_fallback": True}}
        )
        is False
    )


def test_reasoning_llm_loop_streams_on_first_attempt(base_state, monkeypatch):
    """Regression: first LLM pass must use stream_structured so thinking_delta is emitted."""
    from app.nodes.reasoning_node import _run_reasoning_llm_loop
    from app.services.resource_budget import budget_context_from_state, init_task_budget

    calls: list[str] = []

    def _stream_structured(*_a, **_k):
        calls.append("stream")

        def _gen():
            yield '{"summary": "ok", "confidence": 0.9, "structured": {}}'

        return _gen()

    def _invoke_structured(*_a, **_k):
        calls.append("invoke")
        return {"summary": "silent", "confidence": 0.9, "structured": {}}

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
        llm_purpose="reasoning",
        mode="default",
        budget_ctx=budget_ctx,
    )
    assert calls == ["stream"]


def test_planning_gate_replans_on_misaligned_route_audit(base_state):
    state = merge_state(
        base_state,
        input_payload={
            "goal": "write code",
            "route_audit": {"aligned": False, "issues": ["misroute"]},
        },
        plan=["step"],
        status=TaskStatus.PLANNED.value,
    )
    assert route_after_incremental_planning(state) == "incremental_planning"


def test_reflect_turn_audit_strips_retry_flags(isolated_stores, monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_client.invoke_structured",
        lambda *a, **k: {
            "critique": "ok",
            "retry_reasoning": True,
            "retry_planning": True,
            "issues": [],
        },
    )
    state = merge_state(
        create_initial_state(task_id="audit-1"),
        status=TaskStatus.COMPLETED.value,
        final_answer="hi",
        reasoning_result={"summary": "hi", "confidence": 0.9, "structured": {}},
        input_payload={"goal": "test"},
    )
    out = reflect_turn_audit_sync(state)
    reflection = out.get("reflection_result") or {}
    assert reflection.get("retry_reasoning") is False
    assert reflection.get("retry_planning") is False
    assert reflection.get("async_audit") is True
