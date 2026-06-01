from __future__ import annotations

from typing import Any, Optional

from app.domain.packs.base import DomainPack
from app.runtime.state import AgentState
from app.services.action_resolver import CandidateAction, TaskSnapshot


class AnalysisPack(DomainPack):
    """Data analysis / exploration missions."""

    def build_task_snapshot(self, state: AgentState, mission: dict[str, Any]) -> TaskSnapshot:
        progress = state.get("progress") or {}
        metrics = progress.get("metrics") or {}
        observation = state.get("observation") or {}
        tools_used = int(metrics.get("tools_used") or observation.get("tool_count") or 0)
        has_report = bool(metrics.get("report_ready") or state.get("reasoning_result"))
        return TaskSnapshot(
            has_bootstrap_artifact=tools_used > 0 or bool(state.get("retrieved_knowledge")),
            has_main_artifact=has_report,
            main_artifact_bytes=int(metrics.get("report_bytes") or 0),
            target_bytes=int((mission.get("success_criteria") or {}).get("target") or 1),
            step_index=int(state.get("mission_step") or 1),
            forced_intervention=None,
            pending_review=False,
            failure_count=int(state.get("retry_count") or 0) + len(state.get("errors") or []),
        )

    def candidate_actions(self, snapshot: TaskSnapshot) -> list[CandidateAction]:
        if not snapshot.has_bootstrap_artifact:
            return [
                CandidateAction("gather_context", True, 0.9, "need retrieved context"),
                CandidateAction("run_tools", True, 0.85, "run analysis tools"),
            ]
        if not snapshot.has_main_artifact:
            return [CandidateAction("analyze", True, 0.85, "analysis not complete")]
        return [CandidateAction("conclude", True, 0.75, "synthesize conclusion")]

    def map_action_to_intent(
        self,
        action: CandidateAction,
        state: AgentState,
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        step = int(state.get("mission_step") or 1)
        if action.action in ("gather_context", "run_tools"):
            return {
                "enabled": False,
                "action": action.action,
                "source": "analysis_pack",
                "mission_step": step,
            }
        return {
            "enabled": True,
            "action": action.action,
            "source": "analysis_pack",
            "mission_step": step,
            "title": action.action,
        }

    def resolve_artifact_names(
        self,
        policy: dict[str, Any],
        *,
        mission: Optional[dict[str, Any]] = None,
    ) -> dict[str, str]:
        return {
            "bootstrap_artifact": str(policy.get("schema_artifact") or "analysis_schema.json"),
            "main_artifact": str(policy.get("report_artifact") or "analysis_report.md"),
        }


ANALYSIS_PACK = AnalysisPack(
    name="analysis",
    description="General analysis and reasoning for cross-domain questions",
    tools=["echo", "calculator", "get_runtime_info"],
    planning_hints=["gather_context", "analyze", "conclude"],
    system_prompt=(
        "You are a general analysis agent. Synthesize available information and "
        "produce clear, structured conclusions."
    ),
    risk_level="LOW",
    default_max_steps=20,
    metadata={
        "domains": ["analysis", "qa", "general"],
        "capabilities": ["analysis", "reasoning", "hypothesis", "exploration"],
    },
)
