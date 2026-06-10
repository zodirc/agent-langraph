from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.runtime.state import AgentState
    from app.services.action_resolver import CandidateAction, TaskSnapshot


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
        block = mission_block or {}
        goal = str(payload.get("goal") or block.get("objective") or "").strip()
        return {
            "id": state["task_id"],
            "kind": self.name,
            "objective": block.get("objective") or goal,
            "success_criteria": {"type": "steps_done", "target": 1},
            "constraints": dict(block.get("constraints") or {}),
            "execution_mode": block.get("execution_mode", self.default_execution_mode),
            "budget": {"max_steps": self.default_max_steps},
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

    def candidate_actions(self, snapshot: "TaskSnapshot") -> list["CandidateAction"]:
        """Return possible next actions for the current snapshot. Override per domain."""
        return []

    def build_task_snapshot(
        self,
        state: "AgentState",
        mission: dict[str, Any],
    ) -> "TaskSnapshot":
        """Build domain snapshot; override for non-writing packs."""
        from app.services.action_resolver import TaskSnapshot

        progress = state.get("progress") or {}
        metrics = progress.get("metrics") or {}
        forced = None
        target = int((mission.get("success_criteria") or {}).get("target") or 0)
        failure_count = int(state.get("retry_count") or 0) + len(state.get("errors") or [])
        return TaskSnapshot(
            has_bootstrap_artifact=bool(metrics.get("bootstrap_ready")),
            has_main_artifact=bool(metrics.get("main_ready")),
            main_artifact_bytes=int(metrics.get("main_bytes") or 0),
            target_bytes=target,
            step_index=int(state.get("mission_step") or 1),
            forced_intervention=forced,
            pending_review=False,
            failure_count=failure_count,
        )

    def resolve_artifact_names(
        self,
        policy: dict[str, Any],
        *,
        mission: Optional[dict[str, Any]] = None,
    ) -> dict[str, str]:
        """Return bootstrap/main artifact filenames for the domain."""
        return {
            "bootstrap_artifact": str(policy.get("bootstrap_artifact") or "bootstrap.txt"),
            "main_artifact": str(policy.get("main_artifact") or "output.txt"),
        }

    def map_action_to_intent(
        self,
        action: "CandidateAction",
        state: "AgentState",
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        """Map abstract action to domain execution intent (e.g. writing_intent)."""
        return {"enabled": False, "action": action.action, "source": "domain_pack"}

    def map_intent_to_work_item(
        self,
        intent: dict[str, Any],
        mission: dict[str, Any],
        *,
        step: int,
    ) -> Optional[dict[str, Any]]:
        """Map resolved execution intent to a lazy work_plan item."""
        action = str(intent.get("action") or "step")
        if not intent.get("enabled") and action in ("human_gate", "run_tools", "edit_plot"):
            if action == "human_gate":
                return {
                    "id": f"wi-gate-{step}",
                    "kind": "human_gate",
                    "title": "human_gate",
                    "status": "pending",
                    "params": {},
                }
            return {
                "id": f"wi-step-{step}",
                "kind": action,
                "title": action,
                "status": "pending",
                "params": dict(intent.get("params") or intent.get("edit_spec") or {}),
            }
        if not intent.get("enabled"):
            return None
        return {
            "id": f"wi-step-{step}",
            "kind": action,
            "title": str(intent.get("title") or action),
            "status": "pending",
            "params": {
                k: v
                for k, v in intent.items()
                if k not in ("enabled", "action", "source", "title")
            },
        }

    def map_action_to_work_item(
        self,
        action: "CandidateAction",
        mission: dict[str, Any],
        *,
        step: int,
        state: "AgentState",
    ) -> Optional[dict[str, Any]]:
        intent = self.map_action_to_intent(action, state, mission)
        item = self.map_intent_to_work_item(intent, mission, step=step)
        if item is not None:
            return item
        return {
            "id": f"wi-step-{step}",
            "kind": action.action,
            "title": action.action,
            "status": "pending",
            "params": {"reason": action.reason, "score": action.score},
        }

    def work_item_to_intent(
        self,
        item: dict[str, Any],
        *,
        mission: dict[str, Any],
        mission_step: int,
    ) -> dict[str, Any]:
        """Map work_plan item back to execution intent. Override per domain."""
        kind = str(item.get("kind") or "step")
        params = dict(item.get("params") or {})
        return {
            "enabled": kind not in ("human_gate", "run_tools", "edit_plot"),
            "action": kind,
            "source": "work_plan",
            "mission_step": mission_step,
            "work_item_id": item.get("id"),
            "work_item_title": item.get("title"),
            **params,
        }
