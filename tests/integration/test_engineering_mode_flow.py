"""§15.2 engineering_mode graph integration (mocked LLM)."""

from unittest.mock import patch

from app.runtime.graph import run_graph
from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.project_verify.backends import ProjectVerifyResult


def _engineering_plan():
    return {
        "summary": "2048 已生成",
        "preview": "打开 games/demo/index.html",
        "files": [
            {
                "path": "games/demo/index.html",
                "content": "<!DOCTYPE html><html><body><div id=g></div><script src=game.js></script></body></html>",
            },
            {"path": "games/demo/game.js", "content": "const g = [];\n"},
            {"path": "games/demo/style.css", "content": "body{margin:0}\n"},
        ],
    }


@patch("app.services.engineering_execution._generate_files_via_llm")
@patch("app.services.engineering_execution.verify_project")
def test_graph_routes_engineering_mode_2048(mock_verify, mock_eng_gen, isolated_stores):
    mock_eng_gen.return_value = _engineering_plan()
    mock_verify.return_value = ProjectVerifyResult(
        ok=True, backend="web_html_js", status="ok", stage="syntax_check"
    )

    def _fake_planning(state):
        payload = dict(state.get("input_payload") or {})
        payload["route_audit"] = {
            "inferred_kind": "interactive_app",
            "kind_confidence": 0.85,
            "aligned": True,
        }
        payload["target_mode"] = "engineering_mode"
        payload["intent_kind"] = "interactive_app"
        return merge_state(
            state,
            plan=["engineering deliverable"],
            skip_retrieval=True,
            input_payload=payload,
            status=TaskStatus.PLANNED.value,
        )

    state = create_initial_state(
        task_id="eng-graph-2048",
        input_payload={"goal": "做一个浏览器 2048 小游戏，要能直接打开", "risk_level": "LOW"},
    )

    with patch("app.nodes.planning_node.planning_node", _fake_planning):
        final = run_graph(state)

    payload = final.get("input_payload") or {}
    assert payload.get("target_mode") == "engineering_mode"
    assert "文件清单" in (final.get("final_answer") or "")
    trace = payload.get("engineering_trace") or {}
    assert trace.get("verify_backend") == "web_html_js"
    assert final["status"] in (
        TaskStatus.REASONED.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.POLICY_CHECKED.value,
    )


@patch("app.services.engineering_execution._generate_files_via_llm")
@patch("app.services.engineering_execution.verify_project")
def test_cpp_demo_verify_and_repair(mock_verify, mock_gen, isolated_stores):
    mock_gen.side_effect = [
        {
            "summary": "cpp",
            "preview": "compile main.cpp",
            "files": [{"path": "main.cpp", "content": "int main() { return 0; }"}],
        },
        {
            "summary": "cpp fixed",
            "preview": "compile main.cpp",
            "files": [{"path": "main.cpp", "content": "int main() { return 0; }\n"}],
        },
    ]
    mock_verify.side_effect = [
        ProjectVerifyResult(
            ok=False, backend="cpp", status="failed", stderr="error: expected ';'"
        ),
        ProjectVerifyResult(ok=True, backend="cpp", status="ok"),
    ]

    from app.services.engineering_execution import run_engineering_bounded

    state = create_initial_state(
        task_id="eng-cpp-1",
        input_payload={"goal": "用 C++ 写可编译单文件", "intent_kind": "code"},
    )
    result = run_engineering_bounded(state)
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("repair_attempts", 0) >= 1
    assert mock_verify.call_count >= 2


@patch("app.services.engineering_execution._generate_files_via_llm")
@patch("app.services.engineering_execution.verify_project")
def test_makefile_demo_backend(mock_verify, mock_gen, isolated_stores):
    mock_gen.return_value = {
        "summary": "demo project",
        "preview": "make demo",
        "files": [
            {"path": "projects/demo/Makefile", "content": "demo:\n\t@echo ok\n"},
            {"path": "projects/demo/src/main.cpp", "content": "int main(){return 0;}\n"},
        ],
    }
    mock_verify.return_value = ProjectVerifyResult(
        ok=True, backend="make_cpp_demo", status="ok", stage="build"
    )

    from app.services.engineering_execution import run_engineering_bounded

    state = create_initial_state(
        task_id="eng-make-1",
        input_payload={"goal": "小项目 Makefile demo", "intent_kind": "small_project"},
    )
    result = run_engineering_bounded(state)
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("verify_backend") == "make_cpp_demo"
    mock_verify.assert_called()
    assert mock_verify.call_args.kwargs.get("backend_id") == "make_cpp_demo" or (
        mock_verify.call_args[1].get("backend_id") == "make_cpp_demo"
        if mock_verify.call_args[1]
        else True
    )
