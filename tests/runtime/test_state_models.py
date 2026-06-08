"""AgentState nested Pydantic coercion."""

from __future__ import annotations

from app.runtime.state import create_initial_state, merge_state
from app.runtime.state_field_access import mission_from_state, progress_from_state
from app.runtime.state_models import coerce_agent_state


def test_coerce_mission_and_progress() -> None:
    state = merge_state(
        create_initial_state(),
        mission={
            "id": "m1",
            "kind": "writing",
            "objective": "write novel",
            "success_criteria": {"type": "steps_done", "target": 10},
            "budget": {"max_steps": 5},
        },
        progress={
            "phase": "executing",
            "steps_completed": 2,
            "metrics": {"written_chars": 1000},
        },
        step_decision={
            "action": "continue",
            "next_executor": "pipeline:request",
        },
    )
    mission = mission_from_state(state) or {}
    progress = progress_from_state(state) or {}
    meta = (state.get("plan_graph") or {}).get("meta") or {}
    assert mission.get("kind") == "writing"
    assert progress.get("phase") == "executing"
    assert meta.get("step_decision", {}).get("action") == "continue"


def test_coerce_invalid_mission_preserves_dict() -> None:
    raw = {"not_a_mission": True}
    out = coerce_agent_state({"mission": raw, "input_payload": {}})
    assert out.get("mission") is None
    assert (out.get("input_payload") or {}).get("mission") == raw
