from app.services.mission_schema import (
    build_mission_dict,
    estimate_writing_max_steps,
    resolve_mission_budget_dict,
)
from app.runtime.state import create_initial_state


def test_estimate_steps_from_120w_target():
    block = {
        "total_target_chars": 1_200_000,
        "step_policy": {"chars_per_step": 4000, "first_step": "outline"},
    }
    assert estimate_writing_max_steps(block) == 301


def test_estimate_capped_at_hard_cap():
    block = {
        "total_target_chars": 10_000_000,
        "step_policy": {"chars_per_step": 4000, "first_step": "outline"},
    }
    assert estimate_writing_max_steps(block) == 500


def test_planner_max_steps_respected_and_capped():
    budget = resolve_mission_budget_dict(
        {
            "kind": "writing",
            "total_target_chars": 1_200_000,
            "step_policy": {"chars_per_step": 4000},
            "budget": {"max_steps": 280},
        },
        kind="writing",
    )
    assert budget["max_steps"] == 280


def test_planner_max_steps_above_cap_clamped():
    budget = resolve_mission_budget_dict(
        {"budget": {"max_steps": 900}},
        kind="writing",
    )
    assert budget["max_steps"] == 500


def test_build_mission_dict_uses_estimate_when_no_budget(base_state):
    state = create_initial_state(task_id="t-budget")
    mission = build_mission_dict(
        state,
        {
            "goal": "长篇",
            "mission": {
                "kind": "writing",
                "total_target_chars": 8000,
                "step_policy": {"chars_per_step": 4000, "first_step": "outline"},
            },
        },
        kind="writing",
    )
    assert mission["budget"]["max_steps"] == 3
