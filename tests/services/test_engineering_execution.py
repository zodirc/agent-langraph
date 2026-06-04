from unittest.mock import patch

from app.runtime.state import create_initial_state, merge_state
from app.services.engineering_execution import run_engineering_bounded
from app.services.mode_resolution import run_mode_resolution_pipeline
from app.services.route_audit.pipeline import run_route_audit_pipeline


def test_engineering_bounded_writes_and_final_answer():
    state = create_initial_state(
        task_id="eng-exec-1",
        input_payload={
            "goal": "做一个 2048 网页游戏",
            "route_audit": {"inferred_kind": "interactive_app", "kind_confidence": 0.9},
        },
    )
    state = merge_state(state, skip_retrieval=True, selected_tools=[])
    state = run_mode_resolution_pipeline(state)

    mock_plan = {
        "summary": "2048 已生成",
        "preview": "打开 games/demo/index.html",
        "files": [
            {
                "path": "games/demo/index.html",
                "content": "<!DOCTYPE html><html><body><div id=g></div></body></html>",
            },
            {"path": "games/demo/game.js", "content": "const grid = [];\n"},
            {"path": "games/demo/style.css", "content": "body { margin: 0; }\n"},
        ],
    }

    with patch(
        "app.services.engineering_execution._generate_files_via_llm",
        return_value=mock_plan,
    ):
        with patch(
            "app.services.engineering_execution.verify_project",
        ) as mock_verify:
            from app.services.project_verify.backends import ProjectVerifyResult

            mock_verify.return_value = ProjectVerifyResult(
                ok=True,
                backend="web_html_js",
                status="ok",
                stage="syntax_check",
            )
            result = run_engineering_bounded(state)

    assert "文件清单" in (result.get("final_answer") or "")
    trace = (result.get("input_payload") or {}).get("engineering_trace") or {}
    assert trace.get("target_mode") == "engineering_mode"
    assert trace.get("execution_path") == "engineering_bounded"
    assert len(trace.get("written_files") or []) >= 3


def test_route_audit_then_mode_resolution_engineering():
    state = create_initial_state(
        task_id="eng-route-1",
        input_payload={
            "goal": "用 Python 写可编译单文件，语法正确",
            "writing_intent": {"enabled": True, "action": "write_body"},
        },
    )
    state = merge_state(
        state,
        skip_retrieval=True,
        selected_tools=["write_text_artifact"],
        plan=["write code"],
    )
    state = run_route_audit_pipeline(state)
    payload = state.get("input_payload") or {}
    assert payload.get("target_mode") == "engineering_mode"
