"""Engineering bounded: backend resolution from written files, non-retryable verify."""

from unittest.mock import patch

from app.runtime.state import create_initial_state
from app.services.engineering_execution import (
    _generate_files_via_llm,
    _refine_intent_kind,
    _resolve_verify_backend,
    _write_files,
    run_engineering_bounded,
)
from app.services.project_verify.backends import ProjectVerifyResult


def test_refine_intent_from_main_cpp():
    assert (
        _refine_intent_kind(
            "general",
            "写一个计算器",
            [{"path": "main.cpp", "content": "int main(){}"}],
        )
        == "code"
    )
    assert _resolve_verify_backend(
        intent_kind="code",
        goal="计算器",
        written=["main.cpp"],
        plan_files=[{"path": "main.cpp", "content": ""}],
    ) == "cpp"


@patch("app.services.engineering_execution.verify_project")
@patch("app.services.engineering_execution._generate_files_via_llm")
def test_general_intent_still_verifies_cpp_after_write(mock_gen, mock_verify):
    mock_gen.return_value = {
        "summary": "计算器",
        "preview": "g++ main.cpp",
        "files": [{"path": "main.cpp", "content": "int main(){return 0;}"}],
    }
    mock_verify.return_value = ProjectVerifyResult(
        ok=True,
        backend="cpp",
        status="ok",
        stage="compile",
    )
    state = create_initial_state(
        task_id="eng-cpp-1",
        input_payload={
            "goal": "用 C++ 写一个简单的计算器程序",
            "intent_kind": "general",
            "route_audit": {"inferred_kind": "general"},
        },
    )
    result = run_engineering_bounded(state)
    mock_verify.assert_called_once()
    _args, kwargs = mock_verify.call_args
    assert kwargs.get("backend_id") == "cpp" or _args[3] == "cpp" if len(_args) > 3 else kwargs.get("backend_id") == "cpp"
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("intent_kind") == "code"
    assert trace.get("verify_backend") == "cpp"
    assert "步数预算已用尽" not in (result.get("final_answer") or "")


def test_refine_intent_from_goal_cpp_keywords():
    assert _refine_intent_kind("general", "用 C++ 实现计算器", []) == "code"


@patch("app.services.llm_client.invoke_structured")
def test_generate_files_fallback_on_llm_error(mock_invoke, isolated_stores):
    mock_invoke.side_effect = RuntimeError("llm down")
    state = create_initial_state(task_id="eng-fb-1", input_payload={"goal": "cpp demo"})
    plan, err = _generate_files_via_llm(state, goal="cpp demo", intent_kind="code")
    assert err
    assert plan.get("files")
    assert plan.get("plan_source") == "fallback_layout"


def test_empty_goal_skips_verify():
    state = create_initial_state(task_id="eng-empty", input_payload={"goal": "  "})
    result = run_engineering_bounded(state)
    assert "非空的 goal" in (result.get("final_answer") or "")


@patch("app.services.engineering_execution.verify_project")
@patch("app.services.engineering_execution._generate_files_via_llm")
def test_partial_write_still_verifies(mock_gen, mock_verify):
    mock_gen.return_value = (
        {
            "summary": "x",
            "preview": "",
            "files": [{"path": "main.cpp", "content": "int main(){return 0;}"}],
        },
        None,
    )
    mock_verify.return_value = ProjectVerifyResult(
        ok=True, backend="cpp", status="ok", stage="compile"
    )
    state = create_initial_state(
        task_id="eng-partial",
        input_payload={"goal": "C++ main", "intent_kind": "code"},
    )
    result = run_engineering_bounded(state)
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("written_files") == ["main.cpp"]
    mock_verify.assert_called_once()


def test_write_files_collects_errors(isolated_stores):
    with patch(
        "app.services.engineering_execution.handle_write_file",
        side_effect=OSError("disk"),
    ):
        wf = _write_files("t1", [{"path": "a.cpp", "content": "x"}])
    assert not wf.written
    assert wf.errors
