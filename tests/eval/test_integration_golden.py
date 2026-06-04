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


def _mock_planning_mission(monkeypatch: pytest.MonkeyPatch) -> None:
    def _inject(result: dict, payload: dict) -> tuple[dict, str | None]:
        payload = dict(payload)
        if payload.get("mission"):
            return payload, None
        payload["mission"] = {"kind": "single_turn"}
        return payload, "golden_handoff"

    monkeypatch.setattr(
        "app.nodes.planning_node.apply_planning_mission_decision", _inject
    )


def _mock_mission_oma(monkeypatch: pytest.MonkeyPatch, test_settings) -> None:
    _mock_mission_writing(monkeypatch, test_settings)
    import json as _json

    def fake_invoke(system, user_payload):
        data = (
            _json.loads(user_payload)
            if isinstance(user_payload, str)
            else user_payload
        )
        return {
            "issues": [],
            "pass": True,
            "summary": "oma review",
            "polish_recommended": False,
        }

    monkeypatch.setattr(
        "app.services.writing_phases._invoke_phase_structured",
        lambda system, payload: fake_invoke(system, payload),
    )
    from app.domain.writing_memory_models import ChapterQualityRubric

    monkeypatch.setattr(
        "app.services.writing_quality.score_chapter_quality",
        lambda **kw: ChapterQualityRubric(
            continuity_score=0.8,
            outline_alignment=0.8,
            character_consistency=0.8,
            duplication_risk=0.1,
            chapter_completion=0.9,
            hook_quality=0.7,
        ),
    )
    monkeypatch.setattr(
        "app.services.fact_bundle_builder._hybrid_retrieve",
        lambda *a, **k: [],
    )


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


def _mock_mission_writing(monkeypatch: pytest.MonkeyPatch, test_settings) -> None:
    import app.services.artifact_tools as art
    from app.domain.packs import writing as writing_mod

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    monkeypatch.setattr(
        writing_mod.WRITING_PACK,
        "suggest_step_decision",
        lambda *a, **k: {
            "action": "continue",
            "next_executor": "subgraph:writing",
            "params": {},
            "rationale": "test",
        },
    )
    fake = "。" * 80
    monkeypatch.setattr(
        "app.nodes.writing_node._generate_validated_content",
        lambda *a, **k: fake,
    )


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
    if case.get("mock_planning_mission"):
        _mock_planning_mission(monkeypatch)
    if case.get("mock_mission_oma"):
        _mock_mission_oma(monkeypatch, test_settings)
    elif case.get("mock_mission_writing"):
        _mock_mission_writing(monkeypatch, test_settings)
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
    min_steps = case.get("expect_min_mission_steps")
    if min_steps is not None:
        step = int(state.get("mission_step") or 0)
        completed = int((state.get("progress") or {}).get("steps_completed") or 0)
        assert max(step, completed) >= int(min_steps)
    if case.get("expect_mission_handoff"):
        assert state.get("mission") or (state.get("input_payload") or {}).get("mission")
    if case.get("expect_oma_fact_bundle"):
        payload = state.get("input_payload") or {}
        fb = payload.get("fact_bundle_id") or (payload.get("fact_bundle") or {}).get(
            "fact_bundle_id"
        )
        from app.services.writing_phases import load_chapter_reviews

        reviews = load_chapter_reviews(state["task_id"])
        has_verdict = any(
            (v.get("evidence") or {}).get("fact_bundle_id")
            for v in (reviews.get("reviews") or {}).values()
            if isinstance(v, dict)
        )
        assert fb or has_verdict, "expected OMAW fact_bundle or reviewed verdict"
    passed = 1.0 if state["status"] == TaskStatus.COMPLETED.value else 0.5
    record(case["id"], passed=passed)
