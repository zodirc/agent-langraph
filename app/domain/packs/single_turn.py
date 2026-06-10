from __future__ import annotations

from typing import Any, Optional

from app.domain.packs.base import DomainPack
from app.runtime.state import AgentState


class SingleTurnPack(DomainPack):
    """One request pipeline per mission — equivalent to legacy single graph run."""

    def parse_mission(
        self,
        state: AgentState,
        payload: dict[str, Any],
        *,
        mission_block: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        block = mission_block or {}
        goal = str(
            payload.get("goal") or payload.get("query") or block.get("objective") or ""
        ).strip()
        return {
            "id": state["task_id"],
            "kind": "single_turn",
            "objective": block.get("objective") or goal,
            "success_criteria": {"type": "steps_done", "target": 1},
            "constraints": dict(block.get("constraints") or {}),
            "execution_mode": block.get("execution_mode", "interactive"),
            "budget": {
                "max_steps": int(block.get("max_steps") or 1),
                "max_wall_sec": int(block.get("max_wall_sec") or 600),
            },
        }

    def collect_metrics(
        self,
        state: AgentState,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "steps": int(state.get("mission_step") or 0),
            "tool_count": observation.get("tool_count", 0),
            "has_reasoning": bool(state.get("reasoning_result")),
        }

    def evaluate_success(
        self,
        mission: dict[str, Any],
        progress: dict[str, Any],
        observation: dict[str, Any],
    ) -> tuple[bool, str]:
        if observation.get("has_failures"):
            return False, "step had failures"
        metrics = progress.get("metrics") or {}
        if progress.get("steps_completed", 0) >= 1 and metrics.get("has_reasoning"):
            return True, "single turn pipeline completed"
        return False, "waiting for first step"

    def suggest_step_decision(
        self,
        state: AgentState,
        *,
        mission: dict[str, Any],
        progress: dict[str, Any],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        if progress.get("steps_completed", 0) >= 1:
            return {
                "action": "finish",
                "next_executor": "pipeline:request",
                "rationale": "single_turn complete",
            }
        return {
            "action": "continue",
            "next_executor": "pipeline:request",
            "params": {},
            "rationale": "run one full request pipeline",
        }


SINGLE_TURN_PACK = SingleTurnPack(
    name="single_turn",
    description="Standard Q&A: one planning→tools→writing?→reasoning pipeline per mission",
    tools=[],
    planning_hints=["analyze_goal", "retrieve_context", "reason_and_answer"],
    system_prompt="Execute one complete request pipeline and finish.",
    risk_level="LOW",
    default_max_steps=1,
    default_execution_mode="interactive",
    metadata={"domains": ["qa", "chat", "default"]},
)
