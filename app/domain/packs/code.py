from __future__ import annotations

from typing import Any, Optional

from app.domain.packs.base import DomainPack
from app.runtime.state import AgentState
from app.services.action_resolver import CandidateAction, TaskSnapshot


class CodePack(DomainPack):
    """Code review / implementation missions."""

    def build_task_snapshot(self, state: AgentState, mission: dict[str, Any]) -> TaskSnapshot:
        structured = (state.get("reasoning_result") or {}).get("structured") or {}
        artifacts = list(structured.get("artifacts") or state.get("artifacts") or [])
        code_items = [a for a in artifacts if isinstance(a, dict) and a.get("kind") == "code"]
        verify_ok = structured.get("code_verify_ok") is True
        return TaskSnapshot(
            has_bootstrap_artifact=bool(code_items),
            has_main_artifact=verify_ok,
            main_artifact_bytes=sum(len(str(a.get("content") or "")) for a in code_items),
            target_bytes=1,
            step_index=int(state.get("mission_step") or 1),
            forced_intervention=None,
            pending_review=False,
            failure_count=int(state.get("retry_count") or 0)
            + len(state.get("errors") or [])
            + (0 if verify_ok else (1 if structured.get("code_verify_failed") else 0)),
        )

    def candidate_actions(self, snapshot: TaskSnapshot) -> list[CandidateAction]:
        if snapshot.failure_count > 0 and snapshot.has_bootstrap_artifact:
            return [CandidateAction("repair", True, 0.95, "verification/repair needed")]
        if not snapshot.has_bootstrap_artifact:
            return [CandidateAction("scaffold", True, 0.9, "code scaffold missing")]
        if not snapshot.has_main_artifact:
            return [
                CandidateAction("implement", True, 0.85, "implementation pending"),
                CandidateAction("verify", True, 0.8, "run compile verify"),
            ]
        return [CandidateAction("verify", True, 0.7, "re-verify code")]

    def map_action_to_intent(
        self,
        action: CandidateAction,
        state: AgentState,
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        step = int(state.get("mission_step") or 1)
        enabled = action.action not in ("scaffold",)
        return {
            "enabled": enabled,
            "action": action.action,
            "source": "code_pack",
            "mission_step": step,
            "title": action.action,
        }

    def resolve_artifact_names(
        self,
        policy: dict[str, Any],
        *,
        mission: Optional[dict[str, Any]] = None,
    ) -> dict[str, str]:
        lang = str(policy.get("language") or "cpp")
        return {
            "bootstrap_artifact": str(policy.get("scaffold_artifact") or f"main.{lang}"),
            "main_artifact": str(policy.get("src_artifact") or f"solution.{lang}"),
        }


CODE_PACK = CodePack(
    name="code",
    description="Code understanding, review, and implementation guidance",
    tools=["echo", "calculator", "get_runtime_info"],
    planning_hints=["analyze_code", "identify_risks", "suggest_fixes"],
    system_prompt=(
        "You are a software engineering specialist. Provide precise technical analysis, "
        "highlight bugs, and suggest minimal safe changes."
    ),
    risk_level="MEDIUM",
    default_max_steps=15,
    metadata={"domains": ["code", "programming", "debug"]},
)
