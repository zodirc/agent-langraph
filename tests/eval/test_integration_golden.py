"""Integration golden tasks (mission handoff, graph completion)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.state import TaskStatus
from app.services.graph_runner import GraphRunner
from tests.eval.eval_metrics import record

_CASES = Path(__file__).parent / "integration_cases.json"


def _load_cases() -> list[dict]:
    if not _CASES.exists():
        return []
    data = json.loads(_CASES.read_text(encoding="utf-8"))
    return list(data.get("cases") or data.get("tasks") or [])


def _mock_supervisor_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import worker_executor

    def fake_parallel(**kwargs: object) -> tuple[dict, list, list, list]:
        subtasks = kwargs["subtasks"]  # type: ignore[index]
        results = {}
        for st in subtasks:
            results[st["subtask_id"]] = {
                "subtask_id": st["subtask_id"],
                "domain": st["domain"],
                "status": "COMPLETED",
                "summary": f"done {st['domain']}",
                "confidence": 0.9,
                "risk_level": "LOW",
            }
        updated = [{**st, "status": "COMPLETED"} for st in subtasks]
        return results, updated, [], []

    monkeypatch.setattr(worker_executor, "run_workers_parallel", fake_parallel)


def _mock_engineering_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = {
        "summary": "engineering golden",
        "preview": "open index.html",
        "files": [
            {
                "path": "games/golden/index.html",
                "content": "<!DOCTYPE html><html><body><script src=game.js></script></body></html>",
            },
            {"path": "games/golden/game.js", "content": "const x = 1;\n"},
            {"path": "games/golden/style.css", "content": "body{margin:0}\n"},
        ],
    }

    def _fake_planning(state):
        from app.runtime.state import TaskStatus, merge_state

        payload = dict(state.get("input_payload") or {})
        goal = str(payload.get("goal") or "")
        kind = "interactive_app"
        if "c++" in goal.lower() or "cpp" in goal.lower():
            kind = "code"
        elif "makefile" in goal.lower() or "make demo" in goal.lower():
            kind = "small_project"
        payload["route_audit"] = {
            "inferred_kind": kind,
            "kind_confidence": 0.9,
            "aligned": True,
        }
        updated = merge_state(
            state,
            plan=["engineering deliverable"],
            skip_retrieval=True,
            input_payload=payload,
            status=TaskStatus.PLANNED.value,
        )
        from app.services.mode_resolution import run_mode_resolution_pipeline

        return run_mode_resolution_pipeline(updated)

    def _fake_engineering(state):
        from app.services.engineering_execution import run_engineering_bounded

        return run_engineering_bounded(state)

    from app.services.project_verify.backends import ProjectVerifyResult

    def _fake_verify(task_id, **kwargs):
        backend = kwargs.get("backend_id") or "web_html_js"
        if backend in (None, ""):
            from app.services.project_verify.backends import resolve_project_backend_id

            backend = resolve_project_backend_id(
                intent_kind=kwargs.get("intent_kind") or "code",
                goal=kwargs.get("goal") or "",
            ) or "web_html_js"
        return ProjectVerifyResult(
            ok=True,
            backend=str(backend),
            status="ok",
            stage="syntax_check",
        )

    monkeypatch.setattr("app.nodes.planning_node.planning_node", _fake_planning)
    monkeypatch.setattr(
        "app.services.engineering_execution._generate_files_via_llm",
        lambda *a, **k: plan,
    )
    monkeypatch.setattr("app.services.engineering_execution.verify_project", _fake_verify)


def _mock_planning_qa_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_planning(state):
        from app.runtime.state import TaskStatus, merge_state

        payload = dict(state.get("input_payload") or {})
        payload["route_audit"] = {
            "inferred_kind": "qa",
            "kind_confidence": 0.8,
            "aligned": True,
        }
        payload["writing_intent"] = {**payload.get("writing_intent", {}), "enabled": True}
        updated = merge_state(
            state,
            plan=["answer question"],
            skip_retrieval=True,
            input_payload=payload,
            status=TaskStatus.PLANNED.value,
        )
        from app.services.mode_resolution import run_mode_resolution_pipeline

        return run_mode_resolution_pipeline(updated)

    monkeypatch.setattr("app.nodes.planning_node.planning_node", _fake_planning)


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_integration_golden(
    case: dict, isolated_stores, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    if case.get("mock_supervisor_workers"):
        _mock_supervisor_workers(monkeypatch)
    if case.get("mock_engineering_bounded"):
        _mock_engineering_bounded(monkeypatch)
    if case.get("mock_planning_qa_mode"):
        _mock_planning_qa_mode(monkeypatch)
    runner = GraphRunner()
    payload = dict(case.get("input") or {})
    mode = str(payload.pop("execution_mode", case.get("execution_mode", "single")))
    state = runner.start_task(
        user_id="golden",
        task_type=str(case.get("task_type", "qa")),
        input_payload=payload,
        execution_mode=mode,
    )
    expected_status = case.get("expected_status")
    if expected_status:
        assert state["status"] in (
            expected_status
            if isinstance(expected_status, list)
            else [expected_status]
        )
    for keyword in case.get("expected_output_contains") or []:
        answer = str(state.get("final_answer") or "")
        assert keyword.lower() in answer.lower(), f"missing {keyword!r} in answer"
    for node in case.get("expected_nodes") or []:
        history = state.get("node_history") or []
        nodes = {str(h.get("node")) for h in history if isinstance(h, dict)}
        if not nodes:
            audit = state.get("audit_log") or []
            nodes = {str(e.get("node")) for e in audit if isinstance(e, dict) and e.get("node")}
        assert node in nodes, f"node {node} not in history/audit {nodes}"
    for forbidden in case.get("forbidden_nodes") or []:
        history = state.get("node_history") or []
        nodes = {str(h.get("node")) for h in history if isinstance(h, dict)}
        audit = state.get("audit_log") or []
        nodes |= {str(e.get("node")) for e in audit if isinstance(e, dict) and e.get("node")}
        assert forbidden not in nodes, f"forbidden node {forbidden} present in {nodes}"
    payload_out = state.get("input_payload") or {}
    if case.get("expect_engineering_mode"):
        assert payload_out.get("target_mode") == "engineering_mode"
    if case.get("expect_target_mode"):
        assert payload_out.get("target_mode") == case["expect_target_mode"]
    if case.get("expect_mode_switch_contains"):
        reason = str(payload_out.get("mode_switch_reason") or "")
        action = str(payload_out.get("mode_switch_action") or "")
        assert case["expect_mode_switch_contains"] in reason or case[
            "expect_mode_switch_contains"
        ] in action
    if case.get("expect_min_written_files") is not None:
        trace = payload_out.get("engineering_trace") or {}
        assert len(trace.get("written_files") or []) >= int(case["expect_min_written_files"])
    if case.get("expect_verify_backend"):
        trace = payload_out.get("engineering_trace") or {}
        assert trace.get("verify_backend") == case["expect_verify_backend"]
    passed = 1.0 if state["status"] == TaskStatus.COMPLETED.value else 0.5
    record(case["id"], passed=passed)
