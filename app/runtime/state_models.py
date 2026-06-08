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
    """Validate/coerce nested dicts and fold legacy top-level containers (WP-4.2)."""
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
    return _strip_legacy_top_level_fields(out)


_LEGACY_TOP_LEVEL_FIELDS = (
    "mission",
    "progress",
    "mission_step",
    "mission_control",
    "exploration",
    "subtasks",
    "worker_results",
    "react_loop",
    "step_decision",
)


def _strip_legacy_top_level_fields(out: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy containers into §2.2 field families, then remove top-level keys."""
    payload = dict(out.get("input_payload") or {})
    plan_graph = dict(out.get("plan_graph") or {"nodes": []})
    meta = dict(plan_graph.get("meta") or {})
    bg = dict(out.get("background_status") or {})

    if isinstance(out.get("mission"), dict):
        payload.setdefault("mission", out["mission"])
        meta["mission"] = out["mission"]
    if isinstance(out.get("progress"), dict):
        bg["progress"] = out["progress"]
        meta["progress"] = out["progress"]
    for key in ("mission_step", "mission_control", "exploration", "subtasks", "worker_results", "react_loop", "step_decision"):
        if out.get(key) is not None:
            meta[key] = out[key]

    if meta:
        plan_graph["meta"] = meta
        out["plan_graph"] = plan_graph
    if payload != out.get("input_payload"):
        out["input_payload"] = payload
    if bg:
        out["background_status"] = bg

    for key in _LEGACY_TOP_LEVEL_FIELDS:
        out.pop(key, None)
    return out
