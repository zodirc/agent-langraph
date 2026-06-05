"""Pydantic models for nested AgentState fields (v0.10 — TypedDict compatible)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domain.mission import Mission, Progress, StepDecision

# Re-export evidence OS models for state boundaries (§7.1).
from app.runtime.evidence_models import (  # noqa: F401
    CandidateEvidence,
    EvidenceConflict,
    EvidencePacket,
    GroundingCheckResult,
    QueryObject,
    RetrievalDecision,
    RetrievalTrace,
)


class MissionStateModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1"
    id: str = ""
    kind: str = "single_turn"
    objective: str = ""
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    execution_mode: str = "interactive"
    budget: dict[str, Any] = Field(default_factory=dict)
    step_policy: Optional[dict[str, Any]] = None
    orchestration: Optional[dict[str, Any]] = None

    def to_mission_dict(self) -> dict[str, Any]:
        if self.id:
            return Mission.from_dict(self.model_dump()).to_dict()
        return self.model_dump()


class ProgressStateModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    phase: str = "pending"
    steps_completed: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    consecutive_failures: int = 0
    work_plan: Optional[dict[str, Any]] = None
    writing_state: Optional[dict[str, Any]] = None
    started_at: str = ""
    updated_at: str = ""

    def to_progress_dict(self) -> dict[str, Any]:
        return Progress.from_dict(self.model_dump()).to_dict()


class StepDecisionStateModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str = "continue"
    next_executor: str = "pipeline:request"
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""

    def to_decision_dict(self) -> dict[str, Any]:
        return StepDecision.from_dict(self.model_dump()).to_dict()


def _looks_like_mission(data: dict[str, Any]) -> bool:
    return any(k in data for k in ("id", "kind", "objective", "success_criteria", "budget"))


def _looks_like_progress(data: dict[str, Any]) -> bool:
    return any(
        k in data
        for k in ("phase", "steps_completed", "metrics", "blockers", "writing_state", "work_plan")
    )


def _looks_like_step_decision(data: dict[str, Any]) -> bool:
    return any(k in data for k in ("action", "next_executor", "params", "rationale"))


def coerce_agent_state(state: dict[str, Any]) -> dict[str, Any]:
    """Validate/coerce mission, progress, step_decision nested dicts in-place."""
    out = dict(state)
    mission = out.get("mission")
    if isinstance(mission, dict) and _looks_like_mission(mission):
        try:
            out["mission"] = MissionStateModel.model_validate(mission).to_mission_dict()
        except Exception:
            pass
    progress = out.get("progress")
    if isinstance(progress, dict) and _looks_like_progress(progress):
        try:
            out["progress"] = ProgressStateModel.model_validate(progress).to_progress_dict()
        except Exception:
            pass
    step_decision = out.get("step_decision")
    if isinstance(step_decision, dict) and _looks_like_step_decision(step_decision):
        try:
            out["step_decision"] = StepDecisionStateModel.model_validate(
                step_decision
            ).to_decision_dict()
        except Exception:
            pass
    return out
