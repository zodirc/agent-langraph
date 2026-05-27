"""Policy routing — ESCALATE must reach human_review; CONTINUE goes via output_guard."""

import pytest

from app.runtime.router import (
    route_after_policy,
    route_after_policy_to_guard,
    route_after_reflection,
    should_reflect,
)
from app.runtime.state import merge_state


@pytest.mark.parametrize(
    "policy_result,expected",
    [
        ("CONTINUE", "output"),
        ("REVIEW", "human_review"),
        ("ESCALATE", "human_review"),
        ("REJECT", "rejected"),
    ],
)
def test_route_after_policy(base_state, policy_result, expected):
    state = merge_state(base_state, policy_result=policy_result)
    assert route_after_policy(state) == expected


@pytest.mark.parametrize(
    "policy_result,expected",
    [
        ("CONTINUE", "output_guard"),
        ("REVIEW", "human_review"),
        ("ESCALATE", "human_review"),
        ("REJECT", "rejected"),
    ],
)
def test_route_after_policy_to_guard(base_state, policy_result, expected):
    state = merge_state(base_state, policy_result=policy_result)
    assert route_after_policy_to_guard(state) == expected


def test_should_reflect_on_route_misalignment(base_state):
    state = merge_state(
        base_state,
        input_payload={
            "goal": "implement cpp",
            "route_audit": {"aligned": False, "issues": ["misroute"]},
        },
        reasoning_result={"summary": "ok", "confidence": 0.9, "structured": {}},
        reflection_count=0,
    )
    assert should_reflect(state) is True


def test_should_reflect_writing_intent(base_state):
    state = merge_state(
        base_state,
        input_payload={
            "goal": "写小说",
            "writing_intent": {"enabled": True, "action": "write_body"},
        },
        reasoning_result={"summary": "ok", "confidence": 0.9, "structured": {}},
        reflection_count=0,
    )
    assert should_reflect(state) is True


def test_should_reflect_skips_when_max_rounds(base_state):
    state = merge_state(
        base_state,
        input_payload={"writing_intent": {"enabled": True}},
        reasoning_result={"summary": "ok", "confidence": 0.9, "structured": {}},
        reflection_count=2,
    )
    assert should_reflect(state) is False


def test_route_after_reflection_retry_planning(base_state):
    state = merge_state(
        base_state,
        reflection_result={"retry_planning": True},
        reflection_count=1,
        planning_revision_count=0,
    )
    assert route_after_reflection(state) == "planning"


def test_route_after_reflection_retry(base_state):
    state = merge_state(
        base_state,
        reflection_result={"retry_reasoning": True},
        reflection_count=1,
    )
    assert route_after_reflection(state) == "reasoning"


def test_route_after_reflection_to_policy(base_state):
    state = merge_state(
        base_state,
        reflection_result={"retry_reasoning": False},
        reflection_count=1,
    )
    assert route_after_reflection(state) == "policy"
