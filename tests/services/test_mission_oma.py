"""Tests for Mission OMAW (ADR-001)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.domain.fact_bundle import FactBundle
from app.domain.review_verdict import ReviewEvidence, ReviewVerdict
from app.domain.worker_execution_policy import policy_for_work_item, resolve_agent_capability
from app.runtime.state import create_initial_state, merge_state
from app.services.mission_oma.intent_spec import IntentSpec, intent_spec_from_payload
from app.services.mission_oma.orchestrator import (
    build_turn_envelope,
    check_acceptance,
    mechanical_step_decision,
    should_use_mission_oma,
)
from app.services.fact_bundle_builder import build_fact_bundle


def _writing_state(**overrides) -> dict:
    base = create_initial_state(
        task_id="oma-test-1",
        user_id="u1",
        input_payload={"goal": "写小说", "execution_mode": "mission_oma"},
    )
    mission = {
        "kind": "writing",
        "total_target_chars": 100000,
        "orchestration": {"enabled": True},
        "step_policy": {"chars_per_step": 4000},
    }
    state = merge_state(
        base,
        execution_mode="mission_oma",
        mission=mission,
        progress={
            "metrics": {"written_chars": 5000, "last_chapter_index": 1},
            "work_plan": {
                "version": 1,
                "mode": "explicit",
                "items": [
                    {
                        "id": "wi-review-1",
                        "kind": "review_chapter",
                        "title": "Review ch1",
                        "status": "pending",
                        "params": {"chapter_index": 1},
                    }
                ],
                "current_id": "wi-review-1",
                "completed_ids": [],
                "total_items": 1,
            },
        },
    )
    if overrides:
        state = merge_state(state, **overrides)
    return state


def test_resolve_agent_capability():
    assert resolve_agent_capability("review_chapter") == ("reviewer", "review_chapter")
    assert resolve_agent_capability("append_body") == ("writer", "write_chapter")
    assert resolve_agent_capability("write_outline") == ("writer", "write_outline")


def test_review_verdict_qualified_requires_fact_bundle():
    v = ReviewVerdict(
        chapter_index=1,
        qualified=True,
        model_pass=True,
        evidence=ReviewEvidence(fact_bundle_id="fb-1"),
    )
    assert v.is_valid()
    v2 = ReviewVerdict(chapter_index=1, qualified=True, model_pass=True)
    assert not v2.is_valid()


def test_review_verdict_from_phase_result():
    verdict = ReviewVerdict.from_phase_result(
        chapter_index=2,
        phase_result={"pass": True, "issues": [], "polish_recommended": False},
        rubric_dict={"composite_score": 0.8},
        fact_bundle_id="fb-x",
        rag_meta={"rag_used": True, "outline_slice": True},
    )
    assert verdict.qualified
    assert verdict.evidence.fact_bundle_id == "fb-x"


def test_save_review_verdict_requires_fact_bundle_id():
    from app.domain.review_verdict import save_review_verdict

    bad = ReviewVerdict(chapter_index=1, qualified=True, model_pass=True)
    with pytest.raises(ValueError, match="fact_bundle_id"):
        save_review_verdict("t1", bad)


def test_should_use_mission_oma_writing():
    state = _writing_state()
    assert should_use_mission_oma(state)


def test_mechanical_step_decision_review():
    state = _writing_state()
    decision = mechanical_step_decision(state)
    assert decision.action == "continue"
    assert decision.next_executor == "subgraph:writing"
    assert decision.params.get("writing_phase") == "review_chapter"
    assert decision.params.get("oma_agent") == "reviewer"


def test_build_turn_envelope():
    state = _writing_state()
    env = build_turn_envelope(state)
    assert env["dispatch"]["to_agent"] == "reviewer"
    assert env["dispatch"]["capability"] == "review_chapter"


def test_build_fact_bundle_structure():
    state = _writing_state()
    with patch("app.services.fact_bundle_builder._hybrid_retrieve", return_value=[]):
        with patch(
            "app.services.fact_bundle_builder._collect_layer_sources",
            return_value=([], []),
        ):
            bundle = build_fact_bundle(
                state, agent="reviewer", capability="review_chapter", chapter_index=1
            )
    assert bundle.get("fact_bundle_id")
    assert bundle.get("capability") == "review_chapter"
    fb = FactBundle.from_dict(bundle)
    assert fb is not None


def test_intent_spec_batch_from_payload():
    payload = {
        "mission_intervention": {"action": "batch_unit_quality", "force": True},
        "turn_contract": {"primary_op": "batch_unit_quality"},
        "mission": {"kind": "writing"},
    }
    spec = intent_spec_from_payload(payload)
    assert spec.kind == "batch_review"


def test_check_acceptance_no_scope():
    state = _writing_state()
    ok, reason = check_acceptance(state, IntentSpec())
    assert ok
    assert reason == "no_scope"


def test_policy_for_work_item_reviewer():
    pol = policy_for_work_item("review_chapter")
    assert pol.agent == "reviewer"
    assert "outline" in pol.retrieval.must_include
    assert "retrieve_knowledge" in pol.react.allowed_actions


def test_upsert_chapter_facts_to_knowledge_store(isolated_stores):
    from app.domain.writing_memory_models import ChapterOutcome, NarrativeEvent
    from app.services.writing_knowledge_index import (
        chapter_facts_doc_id,
        load_chapter_facts_from_store,
        upsert_chapter_facts_for_outcome,
    )

    outcome = ChapterOutcome(
        chapter_index=3,
        events=[
            NarrativeEvent(
                event_id="e1",
                chapter_index=3,
                event_type="plot_point_advanced",
                subject="第3章",
                detail="主角抵达",
            )
        ],
        chapter_summary="主角抵达新城。",
        ending_state="城门关闭",
        hook_for_next="密信出现",
    )
    doc_id = upsert_chapter_facts_for_outcome("task-ch-facts", outcome)
    assert doc_id == chapter_facts_doc_id("task-ch-facts", 3)
    stored = load_chapter_facts_from_store("task-ch-facts", 3)
    assert stored and "主角抵达新城" in stored


def test_parallel_review_subtasks_twelve_chapters():
    from app.services.mission_oma.orchestrator import parallel_review_subtasks
    from app.runtime.state import merge_state, create_initial_state

    chapters = list(range(1, 13))
    state = merge_state(
        create_initial_state(task_id="oma-12"),
        input_payload={
            "intent_spec": {
                "kind": "batch_review",
                "scope": {"chapters": chapters, "mode": "score_and_fix"},
            },
            "mission": {"kind": "writing"},
        },
        manuscript={"body_path": "novel.txt", "outline_path": "outline.txt"},
    )
    subtasks = parallel_review_subtasks(state)
    assert subtasks is not None
    assert len(subtasks) == 12
    assert {st["chapter_index"] for st in subtasks} == set(chapters)
