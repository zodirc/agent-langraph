"""Cursor-style delivery: honest verify text, file-backed writing, conversational QA routing."""

from app.services.engineering_execution import (
    _degraded_reason_for_verify,
    format_engineering_answer,
)
from app.services.mode_router import refine_mode_for_session_switch, resolve_target_mode
from app.services.mode_resolution import should_route_engineering_execution
from app.services.project_verify.backends import ProjectVerifyResult
from app.services.writing_delivery import (
    clear_reasoning_regen_after_persist,
    should_force_slow_reasoning_after_write,
    writing_tool_results_ok,
)
from app.runtime.state import create_initial_state, merge_state


def test_degraded_reason_empty_when_verify_ok():
    verify = ProjectVerifyResult(ok=True, backend="cpp", status="ok", stage="compile")
    reason = _degraded_reason_for_verify(
        verify,
        intent_kind="code",
        backend_id="cpp",
        budget_exhausted=False,
        prior="",
    )
    assert reason == ""


def test_format_engineering_answer_no_degraded_on_ok():
    verify = ProjectVerifyResult(ok=True, backend="cpp", status="ok", stage="compile")
    text = format_engineering_answer(
        summary="ok",
        written_files=["main.cpp"],
        preview="g++ main.cpp",
        verify_result=verify,
        degraded_reason="",
    )
    assert "status: **ok**" in text
    assert "降级说明" not in text


def test_format_engineering_answer_shows_degraded_when_failed():
    verify = ProjectVerifyResult(
        ok=False,
        backend="cpp",
        status="failed",
        stage="compile",
        stderr="error",
    )
    text = format_engineering_answer(
        summary="x",
        written_files=[],
        preview="",
        verify_result=verify,
        degraded_reason="compile failed",
    )
    assert "降级说明" in text


def test_engineering_mode_hello_routes_qa_not_execution():
    state = create_initial_state(
        task_id="mode-hello-1",
        input_payload={
            "goal": "你好",
            "current_mode": "engineering_mode",
            "target_mode": "engineering_mode",
            "interaction_mode": "engineering",
        },
    )
    intent, mode, note = refine_mode_for_session_switch(
        intent_kind="code",
        target_mode="engineering_mode",
        current_mode="engineering_mode",
        confidence=0.48,
        goal="你好",
        min_kind_score=0.5,
    )
    assert mode == "qa_mode"
    assert "conversational" in note
    state = merge_state(state, input_payload={**state["input_payload"], "target_mode": mode})
    assert should_route_engineering_execution(state) is False


def test_clear_reasoning_regen_after_persist():
    payload = clear_reasoning_regen_after_persist(
        {"force_slow_reasoning": True, "route_audit": {"force_slow_reasoning": True}}
    )
    assert "force_slow_reasoning" not in payload
    assert payload.get("skip_reasoning_after_tools") is True


def test_should_not_force_slow_reasoning_after_tool_write():
    results = [
        {
            "tool": "write_text_artifact",
            "status": "ok",
            "result": {"bytes": 1200, "filename": "outline.txt"},
        }
    ]
    assert writing_tool_results_ok(results) is True
    assert (
        should_force_slow_reasoning_after_write(
            {"force_slow_reasoning": True},
            {"force_slow_reasoning": True, "aligned": False},
            tool_results=results,
        )
        is False
    )
