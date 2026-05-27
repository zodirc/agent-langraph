from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.state import AgentState


@dataclass
class DomainPack:
    """Domain capability bundle — workers, mission parsing, progress, and executors."""

    name: str
    description: str
    tools: list[str]
    planning_hints: list[str]
    system_prompt: str
    risk_level: str = "LOW"
    metadata: dict[str, Any] = field(default_factory=dict)

    # --- Mission runtime (phase 3) ---
    default_max_steps: int = 30
    default_execution_mode: str = "interactive"

    def parse_mission(
        self,
        state: AgentState,
        payload: dict[str, Any],
        *,
        mission_block: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build mission dict from user payload. Override per domain."""
        from app.domain.mission import MissionBudget, SuccessCriteria

        block = mission_block or {}
        goal = str(payload.get("goal") or block.get("objective") or "").strip()
        return {
            "id": state["task_id"],
            "kind": self.name,
            "objective": block.get("objective") or goal,
            "success_criteria": SuccessCriteria(type="steps_done", target=1).to_dict(),
            "constraints": dict(block.get("constraints") or {}),
            "execution_mode": block.get("execution_mode", self.default_execution_mode),
            "budget": MissionBudget(max_steps=self.default_max_steps).to_dict(),
        }

    def collect_metrics(
        self,
        state: AgentState,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract progress.metrics from state/observation."""
        return {"steps": int(state.get("mission_step") or 0)}

    def evaluate_success(
        self,
        mission: dict[str, Any],
        progress: dict[str, Any],
        observation: dict[str, Any],
    ) -> tuple[bool, str]:
        """Return (success, reason)."""
        criteria = mission.get("success_criteria") or {}
        if criteria.get("type") == "steps_done":
            target = int(criteria.get("target", 1))
            steps = int(progress.get("steps_completed", 0))
            if steps >= target and not observation.get("has_failures"):
                return True, f"steps {steps} >= {target}"
        return False, "pack default: not complete"

    def suggest_step_decision(
        self,
        state: AgentState,
        *,
        mission: dict[str, Any],
        progress: dict[str, Any],
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        """Rule-based next step (LLM decide can override later)."""
        return {
            "action": "continue",
            "next_executor": "pipeline:request",
            "params": {},
            "rationale": "default pipeline step",
        }

    def default_executor_for_step(self, mission_step: int) -> str:
        return "pipeline:request"
