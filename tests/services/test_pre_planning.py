"""Pre-planning: mode before planning LLM (Cursor-style interaction_mode)."""

from app.runtime.state import create_initial_state
from app.services.pre_planning import (
    parse_explicit_interaction_mode,
    run_pre_planning_pipeline,
    should_skip_planning_llm,
)
from app.services.route_audit.audit import detect_planned_route


def test_explicit_interaction_mode_engineering():
    assert parse_explicit_interaction_mode({"interaction_mode": "engineering"}) == "engineering"


def test_pre_planning_sets_engineering_mode():
    state = create_initial_state(
        task_id="pre-eng-1",
        input_payload={
            "goal": "做一个浏览器 2048 小游戏，落盘可预览",
            "interaction_mode": "engineering",
        },
    )
    out = run_pre_planning_pipeline(state)
    payload = out.get("input_payload") or {}
    assert payload.get("target_mode") == "engineering_mode"
    assert payload.get("pre_planning_completed") is True


def test_should_skip_planning_llm_for_engineering():
    state = create_initial_state(
        task_id="pre-eng-2",
        input_payload={
            "goal": "用 C++ 写 main.cpp 可编译",
            "interaction_mode": "engineering",
        },
    )
    out = run_pre_planning_pipeline(state)
    assert should_skip_planning_llm(out) is True


def test_detect_planned_route_engineering_bounded():
    state = create_initial_state(
        task_id="pre-eng-3",
        input_payload={"target_mode": "engineering_mode"},
    )
    assert detect_planned_route(state) == "engineering_bounded"
