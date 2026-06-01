from __future__ import annotations

from typing import Any, Optional

from app.domain.packs.base import DomainPack
from app.runtime.state import AgentState
from app.services.action_resolver import CandidateAction, TaskSnapshot


class DocumentPack(DomainPack):
    """Document processing missions."""

    def build_task_snapshot(self, state: AgentState, mission: dict[str, Any]) -> TaskSnapshot:
        progress = state.get("progress") or {}
        metrics = progress.get("metrics") or {}
        return TaskSnapshot(
            has_bootstrap_artifact=bool(state.get("retrieved_knowledge") or metrics.get("source_ready")),
            has_main_artifact=bool(metrics.get("summary_ready") or state.get("final_answer")),
            main_artifact_bytes=int(metrics.get("summary_bytes") or 0),
            target_bytes=int((mission.get("success_criteria") or {}).get("target") or 1),
            step_index=int(state.get("mission_step") or 1),
            forced_intervention=None,
            pending_review=False,
            failure_count=int(state.get("retry_count") or 0) + len(state.get("errors") or []),
        )

    def candidate_actions(self, snapshot: TaskSnapshot) -> list[CandidateAction]:
        if not snapshot.has_bootstrap_artifact:
            return [CandidateAction("retrieve", True, 0.9, "source document missing")]
        if not snapshot.has_main_artifact:
            return [CandidateAction("summarize", True, 0.85, "summary pending")]
        return [CandidateAction("extract", True, 0.75, "extract key points")]

    def map_action_to_intent(
        self,
        action: CandidateAction,
        state: AgentState,
        mission: dict[str, Any],
    ) -> dict[str, Any]:
        step = int(state.get("mission_step") or 1)
        return {
            "enabled": action.action != "retrieve",
            "action": action.action,
            "source": "document_pack",
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
            "bootstrap_artifact": str(policy.get("source_artifact") or "source.md"),
            "main_artifact": str(policy.get("summary_artifact") or "summary.md"),
        }


DOCUMENT_PACK = DocumentPack(
    name="document",
    description="Document parsing, summarization, and knowledge extraction",
    tools=["summarize_text", "echo", "read_text_artifact"],
    planning_hints=["retrieve_context", "summarize_document", "extract_key_points"],
    system_prompt=(
        "You are a document processing specialist. Focus on accurate summaries, "
        "structure extraction, and evidence-backed conclusions from text."
    ),
    risk_level="LOW",
    metadata={"domains": ["document", "text", "report"]},
)
