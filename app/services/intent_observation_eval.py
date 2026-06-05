"""Offline evaluation for intent observation golden cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.intent_observation import IntentObservationResult
from app.runtime.state import create_initial_state
from app.services.intent_observation import build_structural_observation
from app.services.pre_planning import seed_pre_planning_route_audit


@dataclass
class IntentObservationGoldenCase:
    case_id: str
    goal: str
    interaction_mode: str | None
    expected_intent_kind: str
    expected_target_mode: str
    mission_active: bool = False


GOLDEN_CASES: tuple[IntentObservationGoldenCase, ...] = (
    IntentObservationGoldenCase(
        case_id="qa_simple",
        goal="LangGraph 的 checkpoint 是怎么工作的？",
        interaction_mode="chat",
        expected_intent_kind="qa",
        expected_target_mode="qa_mode",
    ),
    IntentObservationGoldenCase(
        case_id="engineering_explicit",
        goal="用 Python 写一个 CLI todo，落盘可运行",
        interaction_mode="engineering",
        expected_intent_kind="engineering",
        expected_target_mode="engineering_mode",
    ),
    IntentObservationGoldenCase(
        case_id="writing_explicit",
        goal="写一部长篇科幻小说，先出大纲",
        interaction_mode="writing",
        expected_intent_kind="writing",
        expected_target_mode="manuscript_mode",
    ),
    IntentObservationGoldenCase(
        case_id="mission_active_continue",
        goal="继续写下一章",
        interaction_mode=None,
        expected_intent_kind="mission_control",
        expected_target_mode="manuscript_mode",
        mission_active=True,
    ),
)

MISSION_GOLDEN_CASES: tuple[IntentObservationGoldenCase, ...] = (
    IntentObservationGoldenCase(
        case_id="mission_continue",
        goal="继续",
        interaction_mode=None,
        expected_intent_kind="mission_control",
        expected_target_mode="manuscript_mode",
        mission_active=True,
    ),
    IntentObservationGoldenCase(
        case_id="mission_steer_replan",
        goal="继续，但先把上一章节奏收紧一些",
        interaction_mode=None,
        expected_intent_kind="mission_control",
        expected_target_mode="manuscript_mode",
        mission_active=True,
    ),
    IntentObservationGoldenCase(
        case_id="mission_confirm",
        goal="确认",
        interaction_mode=None,
        expected_intent_kind="mission_control",
        expected_target_mode="manuscript_mode",
        mission_active=True,
    ),
)

MIXED_PROMPT_GOLDEN_CASES: tuple[IntentObservationGoldenCase, ...] = (
    IntentObservationGoldenCase(
        case_id="mixed_qa_then_fix",
        goal="解释一下这个 bug，然后顺手帮我修掉",
        interaction_mode="auto",
        expected_intent_kind="engineering",
        expected_target_mode="engineering_mode",
    ),
    IntentObservationGoldenCase(
        case_id="mixed_explain_and_write",
        goal="说明一下大纲结构，然后继续写第三章",
        interaction_mode="auto",
        expected_intent_kind="writing",
        expected_target_mode="manuscript_mode",
        mission_active=True,
    ),
)

ALL_GOLDEN_CASES: tuple[IntentObservationGoldenCase, ...] = (
    *GOLDEN_CASES,
    *MISSION_GOLDEN_CASES,
    *MIXED_PROMPT_GOLDEN_CASES,
)


def evaluate_structural_golden(case: IntentObservationGoldenCase) -> dict[str, Any]:
    payload: dict[str, Any] = {"goal": case.goal}
    if case.interaction_mode:
        payload["interaction_mode"] = case.interaction_mode
    state = create_initial_state(task_id=f"io-eval-{case.case_id}", input_payload=payload)
    if case.mission_active:
        state = {
            **state,
            "mission": {"kind": "writing", "objective": "long novel"},
        }
    audit_seed = seed_pre_planning_route_audit(state)
    result = build_structural_observation(
        state,
        route_audit_seed=audit_seed,
        explicit_mode=case.interaction_mode,
    )
    ok_kind = result.intent_kind == case.expected_intent_kind
    ok_mode = result.target_mode == case.expected_target_mode
    return {
        "case_id": case.case_id,
        "passed": ok_kind and ok_mode,
        "expected": {
            "intent_kind": case.expected_intent_kind,
            "target_mode": case.expected_target_mode,
        },
        "actual": result.to_dict(),
    }


def run_structural_baseline() -> dict[str, Any]:
    results = [evaluate_structural_golden(c) for c in ALL_GOLDEN_CASES]
    passed = sum(1 for r in results if r["passed"])
    return {
        "suite": "intent_observation_structural",
        "total": len(results),
        "passed": passed,
        "accuracy": passed / len(results) if results else 0.0,
        "cases": results,
    }


def observation_matches_expected(
    result: IntentObservationResult | dict[str, Any],
    *,
    intent_kind: str,
    target_mode: str,
) -> bool:
    if isinstance(result, IntentObservationResult):
        data = result.to_dict()
    else:
        data = result
    return (
        str(data.get("intent_kind") or "") == intent_kind
        and str(data.get("target_mode") or "") == target_mode
    )
