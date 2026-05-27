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
    if case.get("mock_planning_mission"):
        _mock_planning_mission(monkeypatch)
    if case.get("mock_mission_writing"):
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
    min_steps = case.get("expect_min_mission_steps")
    if min_steps is not None:
        step = int(state.get("mission_step") or 0)
        completed = int((state.get("progress") or {}).get("steps_completed") or 0)
        assert max(step, completed) >= int(min_steps)
    if case.get("expect_mission_handoff"):
        assert state.get("mission") or (state.get("input_payload") or {}).get("mission")
    passed = 1.0 if state["status"] == TaskStatus.COMPLETED.value else 0.5
    record(case["id"], passed=passed)
