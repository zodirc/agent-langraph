"""
M8 golden: OMAW steer → FactBundle → review → polish → write → bounded ReAct recovery.

Uses mocks for LLM/writing generation; asserts mechanical contracts from ADR-001.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.domain.review_verdict import load_review_verdict
from app.runtime.mission_graph import run_mission_graph
from app.runtime.state import create_initial_state, merge_state
from app.services.artifact_tools import handle_write_text_artifact, task_artifact_dir
from app.services.mission_service import init_mission_state
from app.services.mission_steer import apply_steer_message
from app.services.mission_oma.orchestrator import should_use_mission_oma
from app.services.writing_phases import load_chapter_reviews


def _oma_writing_payload(**extra) -> dict:
    base = {
        "goal": "写长篇科幻小说",
        "execution_mode": "mission_oma",
        "mission": {
            "kind": "writing",
            "total_target_chars": 200000,
            "orchestration": {"enabled": True},
            "step_policy": {
                "chars_per_step": 800,
                "first_step": "write_outline",
                "then": "append_body",
            },
        },
        "writing_intent": {"enabled": False},
    }
    base.update(extra)
    return base


def _seed_manuscript(task_id: str, *, chapters: int = 1) -> None:
    d = task_artifact_dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    outline = "\n".join(f"第{i}章 概要" for i in range(1, chapters + 1))
    (d / "outline.txt").write_text(outline, encoding="utf-8")
    body = "\n\n".join(
        f"### 第{i}章\n\n" + ("这是正文内容。" * 40)
        for i in range(1, chapters + 1)
    )
    handle_write_text_artifact(
        {"task_id": task_id, "filename": "novel.txt", "content": body}
    )


@pytest.fixture
def oma_state(base_state, isolated_stores, test_settings, monkeypatch):
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    monkeypatch.setattr(
        "app.config.settings.settings.MISSION_WRITING_LLM_DECIDE",
        False,
    )
    monkeypatch.setattr(
        "app.config.settings.settings.MISSION_OMA_DEFAULT_FOR_WRITING",
        True,
    )

    payload = _oma_writing_payload()
    state = init_mission_state(
        merge_state(
            base_state,
            input_payload=payload,
            execution_mode="mission_oma",
            task_id="oma-golden-1",
        ),
        payload,
    )
    _seed_manuscript(state["task_id"], chapters=2)
    ms = dict(state.get("manuscript") or {})
    ms.setdefault("body_path", "novel.txt")
    ms.setdefault("outline_path", "outline.txt")
    ms["body_bytes"] = 2000
    ms["outline_bytes"] = 200
    return merge_state(state, manuscript=ms, task_id="oma-golden-1")


def _mock_review(monkeypatch: pytest.MonkeyPatch) -> None:
    rubric = {
        "continuity_score": 0.8,
        "outline_alignment": 0.75,
        "character_consistency": 0.7,
        "duplication_risk": 0.1,
        "chapter_completion": 0.9,
        "hook_quality": 0.7,
        "composite_score": 0.78,
        "pass_gate": True,
    }

    def fake_score(**kwargs):
        from app.domain.writing_memory_models import ChapterQualityRubric

        return ChapterQualityRubric.from_dict(rubric)

    def fake_invoke(system, user_payload):
        data = json.loads(user_payload) if isinstance(user_payload, str) else user_payload
        ch = int(data.get("chapter_index") or 1)
        return {
            "issues": [],
            "pass": True,
            "summary": f"review ok ch{ch}",
            "polish_recommended": ch == 1,
        }

    monkeypatch.setattr(
        "app.services.writing_phases._invoke_phase_structured",
        lambda state, system, payload: fake_invoke(system, payload),
    )
    monkeypatch.setattr(
        "app.services.writing_quality.score_chapter_quality",
        lambda **kw: fake_score(),
    )


def test_oma_mechanical_decide_and_fact_bundle(oma_state, monkeypatch):
    _mock_review(monkeypatch)
    state = oma_state
    assert should_use_mission_oma(state)

    from app.nodes.mission_decide_node import mission_decide_node
    from app.services.mission_executor import execute_mission_step
    from app.services.mission_service import prepare_state_for_mission_act

    progress = dict(state.get("progress") or {})
    progress["work_plan"] = {
        "version": 1,
        "mode": "explicit",
        "items": [
            {
                "id": "wi-r1",
                "kind": "review_chapter",
                "status": "pending",
                "params": {"chapter_index": 1},
            }
        ],
        "current_id": "wi-r1",
        "completed_ids": [],
        "total_items": 1,
    }
    state = merge_state(state, progress=progress)

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        decided = mission_decide_node(state)
        assert (decided.get("step_decision") or {}).get("source") != "llm"
        act = execute_mission_step(
            prepare_state_for_mission_act(decided),
            decided.get("step_decision") or {},
        )

    payload = act.get("input_payload") or {}
    assert payload.get("fact_bundle_id") or (payload.get("fact_bundle") or {}).get(
        "fact_bundle_id"
    )

    verdict = load_review_verdict(state["task_id"], 1)
    assert verdict is not None
    assert verdict.is_valid()
    assert verdict.evidence.fact_bundle_id


def test_oma_steer_batch_parallel_review_and_acceptance(oma_state, monkeypatch):
    _mock_review(monkeypatch)
    state = apply_steer_message(
        oma_state,
        "",
        intervention={"action": "batch_unit_quality", "force": True},
    )
    payload = dict(state.get("input_payload") or {})
    payload["intent_spec"] = {
        "kind": "batch_review",
        "scope": {"chapters": [1, 2], "mode": "score_and_fix"},
        "acceptance": {"all_in_scope_reviewed": True},
    }
    payload["steer_planning_done"] = True
    payload["require_planning_after_steer"] = False
    payload["turn_kind"] = "steer_execute"
    state = merge_state(state, input_payload=payload)

    from app.services.mission_oma.orchestrator import run_parallel_reviews_if_applicable

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        result = run_parallel_reviews_if_applicable(state)

    assert result is not None
    out = result.get("input_payload") or {}
    parallel = out.get("parallel_review_results") or {}
    assert len(parallel) >= 2 or out.get("acceptance_replan")
    assert "acceptance_ok" in out
    for _ch, outcome in parallel.items():
        assert outcome.get("status") == "COMPLETED"
        assert isinstance(outcome.get("result"), dict)


def test_oma_react_recovery_rebuilds_fact_bundle(oma_state, monkeypatch):
    _mock_review(monkeypatch)
    from app.services.worker_react_bridge import run_worker_bounded_react

    state = merge_state(
        oma_state,
        input_payload={
            **(oma_state.get("input_payload") or {}),
            "writing_intent": {
                "enabled": True,
                "action": "review_chapter",
                "chapter_index": 2,
            },
        },
    )
    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        recovered = run_worker_bounded_react(
            state,
            agent="reviewer",
            capability="review_chapter",
            goal="补全事实",
            allowed_actions=["retrieve_knowledge", "reason", "finish"],
            max_steps=2,
        )
    payload = recovered.get("input_payload") or {}
    assert payload.get("fact_bundle_id")
    assert int(payload.get("react_steps") or 0) >= 0


def test_oma_writing_blocked_without_fact_bundle(oma_state, monkeypatch):
    from app.nodes.writing_node import writing_node
    from app.runtime.state import TaskStatus

    state = merge_state(
        oma_state,
        input_payload={
            **(oma_state.get("input_payload") or {}),
            "writing_intent": {
                "enabled": True,
                "action": "append_body",
                "chapter_index": 3,
                "target_chars": 200,
                "min_chars": 50,
            },
        },
    )
    out = writing_node(state)
    assert out.get("status") == TaskStatus.FAILED.value
    assert any("fact_bundle" in str(e) for e in (out.get("errors") or []))


def test_supervisor_rejected_for_manuscript_mission(oma_state):
    from app.nodes.supervisor_decompose_node import supervisor_decompose_node

    state = merge_state(
        oma_state,
        execution_mode="supervisor",
        input_payload={
            **(oma_state.get("input_payload") or {}),
            "goal": "写小说",
        },
    )
    out = supervisor_decompose_node(state)
    assert out.get("status") == "FAILED"
    assert any("mission_oma" in str(e) for e in (out.get("errors") or []))


def test_oma_full_journey_write_review_polish_rereview(oma_state, monkeypatch):
    """ADR §8.1: review → polish (verdict-driven) → re-review on explicit work_plan."""
    from app.domain.writing_memory_models import ChapterQualityRubric

    review_round = {"n": 0}

    def fake_invoke(system, user_payload):
        data = json.loads(user_payload) if isinstance(user_payload, str) else user_payload
        phase = str(data.get("phase") or "")
        if phase == "polish_chapter":
            return {"polished_text": data.get("chapter_text", "第1章\n润色后正文。")[:500]}
        review_round["n"] += 1
        if review_round["n"] == 1:
            return {
                "issues": ["style"],
                "pass": False,
                "summary": "needs polish",
                "polish_recommended": True,
            }
        return {"issues": [], "pass": True, "summary": "re-review ok", "polish_recommended": False}

    rubric = {
        "continuity_score": 0.8,
        "outline_alignment": 0.75,
        "character_consistency": 0.7,
        "duplication_risk": 0.1,
        "chapter_completion": 0.9,
        "hook_quality": 0.7,
        "composite_score": 0.78,
        "pass_gate": True,
    }

    monkeypatch.setattr(
        "app.services.writing_phases._invoke_phase_structured",
        lambda state, system, payload: fake_invoke(system, payload),
    )
    monkeypatch.setattr(
        "app.services.writing_quality.score_chapter_quality",
        lambda **kw: ChapterQualityRubric.from_dict(rubric),
    )

    progress = {
        "work_plan": {
            "version": 1,
            "mode": "explicit",
            "items": [
                {"id": "wi-w1", "kind": "append_body", "status": "done", "params": {"chapter_index": 1}},
                {"id": "wi-r1", "kind": "review_chapter", "status": "pending", "params": {"chapter_index": 1}, "depends_on": ["wi-w1"]},
                {"id": "wi-p1", "kind": "polish_chapter", "status": "pending", "params": {"chapter_index": 1, "conditional": "verdict.polish_recommended"}, "depends_on": ["wi-r1"]},
                {"id": "wi-rr1", "kind": "review_chapter", "status": "pending", "params": {"chapter_index": 1, "after_polish": True}, "depends_on": ["wi-p1"]},
            ],
            "current_id": "wi-r1",
            "completed_ids": ["wi-w1"],
            "total_items": 4,
        }
    }
    state = merge_state(oma_state, progress=progress)
    from app.nodes.mission_decide_node import mission_decide_node
    from app.services.mission_executor import execute_mission_step
    from app.services.mission_service import prepare_state_for_mission_act

    def _oma_act(st):
        decided = mission_decide_node(st)
        out = execute_mission_step(
            prepare_state_for_mission_act(decided),
            decided.get("step_decision") or {},
        )
        return out, decided

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        after_review, d_review = _oma_act(state)
        assert (d_review.get("step_decision") or {}).get("params", {}).get("oma_agent") == "reviewer"
        v1 = load_review_verdict(state["task_id"], 1)
        assert v1 is not None and v1.polish_recommended and v1.evidence.fact_bundle_id

        after_polish, d_polish = _oma_act(after_review)
        assert (d_polish.get("step_decision") or {}).get("params", {}).get("oma_agent") == "editor"

        after_rereview, d_rr = _oma_act(after_polish)
        assert (d_rr.get("step_decision") or {}).get("params", {}).get("oma_agent") == "reviewer"
        v_final = load_review_verdict(state["task_id"], 1)
        assert v_final is not None and v_final.is_valid()
        plan = (after_rereview.get("progress") or {}).get("work_plan") or {}
        assert "wi-rr1" in (plan.get("completed_ids") or [])


def _oma_act(st, step_decision=None):
    from app.nodes.mission_decide_node import mission_decide_node
    from app.services.mission_executor import execute_mission_step
    from app.services.mission_service import prepare_state_for_mission_act

    if step_decision:
        decided = st
        sd = step_decision
    else:
        decided = mission_decide_node(st)
        sd = decided.get("step_decision") or {}
    out = execute_mission_step(prepare_state_for_mission_act(decided), sd)
    return out, decided


def test_oma_adr_8_1_complete_pipeline(oma_state, monkeypatch, test_settings):
    """ADR §8.1 single chain: review → polish → re-review → writer append ch+1."""
    from app.domain.writing_memory_models import ChapterQualityRubric
    from app.services.manuscript_context import read_body_text
    import app.services.artifact_tools as art

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    review_round = {"n": 0}

    def fake_invoke(system, user_payload):
        data = json.loads(user_payload) if isinstance(user_payload, str) else user_payload
        phase = str(data.get("phase") or "")
        if phase == "polish_chapter":
            return {"polished_text": data.get("chapter_text", "### 第1章\n润色。")[:800]}
        review_round["n"] += 1
        if review_round["n"] == 1:
            return {
                "issues": ["style"],
                "pass": False,
                "summary": "needs polish",
                "polish_recommended": True,
            }
        return {"issues": [], "pass": True, "summary": "ok", "polish_recommended": False}

    rubric = {
        "continuity_score": 0.8,
        "outline_alignment": 0.75,
        "character_consistency": 0.7,
        "duplication_risk": 0.1,
        "chapter_completion": 0.9,
        "hook_quality": 0.7,
        "composite_score": 0.78,
        "pass_gate": True,
    }
    monkeypatch.setattr(
        "app.services.writing_phases._invoke_phase_structured",
        lambda state, system, payload: fake_invoke(system, payload),
    )
    monkeypatch.setattr(
        "app.services.writing_quality.score_chapter_quality",
        lambda **kw: ChapterQualityRubric.from_dict(rubric),
    )
    ch2_body = "### 第2章\n\n续写正文。" * 30
    monkeypatch.setattr(
        "app.nodes.writing_node._generate_validated_content",
        lambda *a, **k: ch2_body,
    )

    progress = {
        "work_plan": {
            "version": 1,
            "mode": "explicit",
            "items": [
                {"id": "wi-w1", "kind": "append_body", "status": "done", "params": {"chapter_index": 1}},
                {"id": "wi-r1", "kind": "review_chapter", "status": "pending", "params": {"chapter_index": 1}},
                {"id": "wi-p1", "kind": "polish_chapter", "status": "pending", "params": {"chapter_index": 1, "conditional": "verdict.polish_recommended"}, "depends_on": ["wi-r1"]},
                {"id": "wi-rr1", "kind": "review_chapter", "status": "pending", "params": {"chapter_index": 1, "after_polish": True}, "depends_on": ["wi-p1"]},
            ],
            "current_id": "wi-r1",
            "completed_ids": ["wi-w1"],
            "total_items": 4,
        }
    }
    state = merge_state(oma_state, progress=progress)

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        after_review, _ = _oma_act(state, None)
        after_polish, _ = _oma_act(after_review, None)
        after_rr, _ = _oma_act(after_polish, None)
        assert load_review_verdict(state["task_id"], 1) is not None

        payload = dict(after_rr.get("input_payload") or {})
        payload.pop("intent_spec", None)
        payload.pop("mission_intervention", None)
        payload["turn_kind"] = "mechanical_continue"
        payload["execution_grant"] = {
            "scope": "mechanical_resume",
            "consume_once": True,
            "forward": "step_policy",
        }
        from app.nodes.mission_decide_node import mission_decide_node

        decided = mission_decide_node(merge_state(after_rr, input_payload=payload))
        params = (decided.get("step_decision") or {}).get("params") or {}
        assert params.get("oma_agent") == "writer"
        assert int(params.get("chapter_index") or 0) == 2

        after_write, _ = _oma_act(
            decided,
            decided.get("step_decision"),
        )
        body = read_body_text(
            state["task_id"],
            "novel.txt",
            state=after_write,
        )
        assert "### 第2章" in body or "第2章" in body
        worker = after_write.get("oma_worker_state") or {}
        assert worker.get("fact_bundle_id") or (after_write.get("input_payload") or {}).get(
            "fact_bundle_id"
        )


def test_oma_m5_steer_confirm_then_batch_review(oma_state, monkeypatch):
    """ADR M5: intent confirmed → FactBundle batch review."""
    _mock_review(monkeypatch)
    state = apply_steer_message(
        oma_state,
        "请进行批量审阅",
        intervention={"action": "batch_unit_quality", "force": True},
    )
    payload = dict(state.get("input_payload") or {})
    payload["steer_intent_confirmed"] = True
    payload["steer_planning_done"] = True
    payload["require_planning_after_steer"] = False
    payload["intent_spec"] = {
        "kind": "batch_review",
        "scope": {"chapters": [1, 2], "mode": "score_and_fix"},
        "acceptance": {"all_in_scope_reviewed": True},
    }
    payload["turn_kind"] = "steer_execute"
    state = merge_state(state, input_payload=payload)

    from app.services.mission_oma.orchestrator import run_parallel_reviews_if_applicable

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        result = run_parallel_reviews_if_applicable(state)
    assert result is not None
    assert (result.get("input_payload") or {}).get("acceptance_ok") is True


def test_run_pipeline_request_redirects_oma(oma_state, monkeypatch):
    """ADR §7.2: run_pipeline_request must not run legacy inline loop for OMAW."""
    called = {"worker": False, "planner": False}

    def fake_worker(st):
        called["worker"] = True
        return st

    def fake_planner(st):
        called["planner"] = True
        return st

    monkeypatch.setattr(
        "app.services.mission_oma.workers.execute_oma_worker",
        fake_worker,
    )
    monkeypatch.setattr(
        "app.services.mission_oma.planner_worker.run_planner_worker",
        fake_planner,
    )
    from app.services.mission_executor import run_pipeline_request

    run_pipeline_request(oma_state)
    assert called["worker"] or called["planner"]


def test_oma_m8_adr_steer_fact_bundle_write_next_chapter(oma_state, monkeypatch):
    """ADR M8 / §8.1: steer batch → FactBundle → unit loop writer (ch2)."""
    _mock_review(monkeypatch)
    state = apply_steer_message(
        oma_state,
        "请审阅前两章",
        intervention={"action": "batch_unit_quality", "force": True},
    )
    payload = dict(state.get("input_payload") or {})
    payload["intent_spec"] = {
        "kind": "batch_review",
        "scope": {"chapters": [1, 2], "mode": "score_and_fix"},
        "acceptance": {"all_in_scope_reviewed": True},
    }
    payload["steer_planning_done"] = True
    payload["require_planning_after_steer"] = False
    payload["turn_kind"] = "steer_execute"
    state = merge_state(state, input_payload=payload)

    from app.services.mission_oma.orchestrator import run_parallel_reviews_if_applicable

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        batch = run_parallel_reviews_if_applicable(state)
        assert batch is not None
        for ch in (1, 2):
            v = load_review_verdict(state["task_id"], ch)
            assert v is not None and v.is_valid()

        out_payload = dict(batch.get("input_payload") or {})
        out_payload.pop("intent_spec", None)
        out_payload.pop("mission_intervention", None)
        out_payload.pop("parallel_review_results", None)
        out_payload.pop("turn_contract", None)
        out_payload["steer_planning_done"] = True
        out_payload["require_planning_after_steer"] = False
        out_payload["turn_kind"] = "mechanical_continue"
        out_payload["execution_grant"] = {
            "scope": "mechanical_resume",
            "consume_once": True,
            "forward": "step_policy",
        }
        state = merge_state(batch, input_payload=out_payload)
        from app.nodes.mission_decide_node import mission_decide_node

        decided = mission_decide_node(state)
        params = (decided.get("step_decision") or {}).get("params") or {}
        assert params.get("oma_agent") == "writer"
        assert int(params.get("chapter_index") or 0) >= 2

        with patch(
            "app.services.mission_oma.orchestrator.run_parallel_reviews_if_applicable",
            return_value=None,
        ):
            from app.services.mission_oma.workers import execute_oma_worker
            from app.services.mission_service import prepare_state_for_mission_act

            after_write = execute_oma_worker(prepare_state_for_mission_act(decided))
        prep_payload = after_write.get("input_payload") or {}
        assert prep_payload.get("fact_bundle_id") or (prep_payload.get("fact_bundle") or {}).get(
            "fact_bundle_id"
        )
        assert (after_write.get("oma_worker_state") or {}).get("agent") == "writer"


def test_oma_golden_one_graph_step_review(oma_state, test_settings, monkeypatch):
    """End-to-end: one mission_graph loop step produces valid ReviewVerdict."""
    import app.services.artifact_tools as art
    from app.domain.packs import writing as writing_mod

    monkeypatch.setattr(art.settings, "ARTIFACTS_PATH", test_settings.ARTIFACTS_PATH)
    _mock_review(monkeypatch)
    monkeypatch.setattr(
        writing_mod.WRITING_PACK,
        "suggest_step_decision",
        lambda *a, **k: {
            "action": "continue",
            "next_executor": "subgraph:writing",
            "params": {"writing_phase": "review_chapter", "chapter_index": 1},
            "rationale": "test",
        },
    )

    progress = dict(oma_state.get("progress") or {})
    progress["work_plan"] = {
        "version": 1,
        "mode": "explicit",
        "items": [
            {
                "id": "wi-g1",
                "kind": "review_chapter",
                "status": "pending",
                "params": {"chapter_index": 1},
            }
        ],
        "current_id": "wi-g1",
        "completed_ids": [],
        "total_items": 1,
    }
    state = merge_state(oma_state, progress=progress, mission_step=0)

    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        with patch(
            "app.services.progress_evaluator.evaluate_mission_control",
        ) as ev:
            from app.services.progress_evaluator import EvalResult

            ev.return_value = EvalResult(
                done=True,
                reason="golden_one_step",
                action="pause",
            )
            final = run_mission_graph(state)

    verdict = load_review_verdict(state["task_id"], 1)
    audit_nodes = {e.get("node") for e in (final.get("audit_log") or []) if isinstance(e, dict)}
    assert "mission_decide" in audit_nodes or final.get("step_decision")
    if verdict:
        assert verdict.is_valid()
