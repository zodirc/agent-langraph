"""
Mission runtime domain model — domain-agnostic long-horizon task contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


MISSION_SCHEMA_VERSION = "1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepPolicy:
    """How the mission advances each control-loop step (domain pack interprets)."""

    unit: str = "chapter"
    chars_per_step: int = 3500
    outline_max_chars: int = 12000
    first_step: str = "outline"
    then: str = "append_body"
    body_artifact: str = "novel.txt"
    outline_artifact: str = "outline.txt"

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "chars_per_step": self.chars_per_step,
            "outline_max_chars": self.outline_max_chars,
            "first_step": self.first_step,
            "then": self.then,
            "body_artifact": self.body_artifact,
            "outline_artifact": self.outline_artifact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepPolicy:
        return cls(
            unit=str(data.get("unit", "chapter")),
            chars_per_step=int(data.get("chars_per_step", 3500)),
            outline_max_chars=int(data.get("outline_max_chars", 12000)),
            first_step=str(data.get("first_step", "outline")),
            then=str(data.get("then", "append_body")),
            body_artifact=str(data.get("body_artifact", "novel.txt")),
            outline_artifact=str(data.get("outline_artifact", "outline.txt")),
        )


@dataclass
class SuccessCriteria:
    """Machine-checkable completion condition."""

    type: str  # metric_gte | steps_done | predicate_true | manual
    metric: str = ""
    target: float = 0.0
    evidence_source: str = "progress"  # progress | observation | external

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "metric": self.metric,
            "target": self.target,
            "evidence_source": self.evidence_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuccessCriteria:
        return cls(
            type=str(data.get("type", "steps_done")),
            metric=str(data.get("metric", "")),
            target=float(data.get("target", 0)),
            evidence_source=str(data.get("evidence_source", "progress")),
        )


@dataclass
class MissionBudget:
    max_steps: int = 500
    max_wall_sec: int = 3600
    max_failures: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_wall_sec": self.max_wall_sec,
            "max_failures": self.max_failures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionBudget:
        return cls(
            max_steps=int(data.get("max_steps", 500)),
            max_wall_sec=int(data.get("max_wall_sec", 3600)),
            max_failures=int(data.get("max_failures", 3)),
        )


@dataclass
class Mission:
    """Persistent task contract (independent of a single user message)."""

    id: str
    kind: str  # single_turn | writing | document | code | ...
    objective: str
    success_criteria: SuccessCriteria
    constraints: dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "interactive"  # interactive | autonomous
    budget: MissionBudget = field(default_factory=MissionBudget)
    step_policy: Optional[StepPolicy] = None
    orchestration: Optional[dict[str, Any]] = None
    schema_version: str = MISSION_SCHEMA_VERSION
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "kind": self.kind,
            "objective": self.objective,
            "success_criteria": self.success_criteria.to_dict(),
            "constraints": dict(self.constraints),
            "execution_mode": self.execution_mode,
            "budget": self.budget.to_dict(),
            "created_at": self.created_at,
        }
        if self.step_policy:
            out["step_policy"] = self.step_policy.to_dict()
        if self.orchestration:
            out["orchestration"] = dict(self.orchestration)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mission:
        sc = data.get("success_criteria") or {}
        budget = data.get("budget") or {}
        sp = data.get("step_policy")
        step_policy = StepPolicy.from_dict(sp) if isinstance(sp, dict) else None
        return cls(
            id=str(data.get("id", "")),
            kind=str(data.get("kind", "single_turn")),
            objective=str(data.get("objective", "")),
            success_criteria=SuccessCriteria.from_dict(sc if isinstance(sc, dict) else {}),
            constraints=dict(data.get("constraints") or {}),
            execution_mode=str(data.get("execution_mode", "interactive")),
            budget=MissionBudget.from_dict(budget if isinstance(budget, dict) else {}),
            step_policy=step_policy,
            orchestration=dict(data["orchestration"])
            if isinstance(data.get("orchestration"), dict)
            else None,
            schema_version=str(data.get("schema_version", MISSION_SCHEMA_VERSION)),
            created_at=str(data.get("created_at", _now_iso())),
        )


@dataclass
class Progress:
    """Observable progress — metrics from environment, not LLM prose."""

    phase: str = "pending"  # pending | executing | completed | paused | failed
    steps_completed: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    work_plan: Optional[dict[str, Any]] = None
    writing_state: Optional[dict[str, Any]] = None
    started_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "phase": self.phase,
            "steps_completed": self.steps_completed,
            "metrics": dict(self.metrics),
            "blockers": list(self.blockers),
            "consecutive_failures": self.consecutive_failures,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }
        if self.work_plan:
            out["work_plan"] = dict(self.work_plan)
        if self.writing_state:
            out["writing_state"] = dict(self.writing_state)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Progress:
        return cls(
            phase=str(data.get("phase", "pending")),
            steps_completed=int(data.get("steps_completed", 0)),
            metrics=dict(data.get("metrics") or {}),
            blockers=list(data.get("blockers") or []),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            work_plan=dict(data["work_plan"]) if isinstance(data.get("work_plan"), dict) else None,
            writing_state=dict(data["writing_state"])
            if isinstance(data.get("writing_state"), dict)
            else None,
            started_at=str(data.get("started_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
        )


@dataclass
class StepDecision:
    """Control output: what to do next in the mission loop."""

    action: str  # continue | finish | pause | escalate | retry
    next_executor: str = "pipeline:request"  # pipeline:request | subgraph:writing | tools_only
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "next_executor": self.next_executor,
            "params": dict(self.params),
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepDecision:
        return cls(
            action=str(data.get("action", "continue")),
            next_executor=str(data.get("next_executor", "pipeline:request")),
            params=dict(data.get("params") or {}),
            rationale=str(data.get("rationale", "")),
        )
