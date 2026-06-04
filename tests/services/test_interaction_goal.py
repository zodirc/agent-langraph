from app.services.interaction_goal import (
    explicit_mode_should_apply,
    goal_is_conversational_qa,
)
from app.services.pre_planning import run_pre_planning_pipeline
from app.runtime.state import create_initial_state


def test_hello_is_conversational():
    assert goal_is_conversational_qa("你好")
    assert goal_is_conversational_qa("  Hello!  ")
    assert not goal_is_conversational_qa("用 C++ 写一个计算器并落盘")
    assert explicit_mode_should_apply("engineering", "你好") is False
    assert explicit_mode_should_apply("engineering", "做一个 2048 网页") is True


def test_pre_planning_engineering_ui_hello_stays_qa(isolated_stores):
    state = create_initial_state(
        task_id="pre-hello-1",
        input_payload={"goal": "你好", "interaction_mode": "engineering"},
    )
    out = run_pre_planning_pipeline(state)
    payload = out.get("input_payload") or {}
    assert payload.get("target_mode") == "qa_mode"
