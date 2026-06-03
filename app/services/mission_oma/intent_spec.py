"""IntentSpec — user steer intent normalization (OMAW)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

IntentKind = Literal[
    "batch_review",
    "continue_write",
    "edit_plot",
    "pause",
    "side_qa",
]


@dataclass
class IntentAcceptance:
    all_in_scope_reviewed: bool = True
    failed_must_polish_or_human: bool = True
    emphasis_dimensions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_in_scope_reviewed": self.all_in_scope_reviewed,
            "failed_must_polish_or_human": self.failed_must_polish_or_human,
            "emphasis_dimensions": list(self.emphasis_dimensions),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> IntentAcceptance:
        if not isinstance(data, dict):
            return cls()
        return cls(
            all_in_scope_reviewed=bool(data.get("all_in_scope_reviewed", True)),
            failed_must_polish_or_human=bool(data.get("failed_must_polish_or_human", True)),
            emphasis_dimensions=[
                str(x) for x in (data.get("emphasis_dimensions") or []) if x
            ],
        )


@dataclass
class IntentScope:
    chapters: list[int] = field(default_factory=list)
    mode: str = "score_and_fix"

    def to_dict(self) -> dict[str, Any]:
        return {"chapters": list(self.chapters), "mode": self.mode}

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> IntentScope:
        if not isinstance(data, dict):
            return cls()
        chapters: list[int] = []
        for c in data.get("chapters") or []:
            try:
                chapters.append(int(c))
            except (TypeError, ValueError):
                continue
        return cls(chapters=chapters, mode=str(data.get("mode") or "score_and_fix"))


@dataclass
class IntentSpec:
    kind: str = "continue_write"
    scope: IntentScope = field(default_factory=IntentScope)
    coverage: str = "user_scope"
    acceptance: IntentAcceptance = field(default_factory=IntentAcceptance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "scope": self.scope.to_dict(),
            "coverage": self.coverage,
            "acceptance": self.acceptance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> IntentSpec:
        if not isinstance(data, dict):
            return cls()
        return cls(
            kind=str(data.get("kind") or "continue_write"),
            scope=IntentScope.from_dict(data.get("scope")),
            coverage=str(data.get("coverage") or "user_scope"),
            acceptance=IntentAcceptance.from_dict(data.get("acceptance")),
        )


def normalize_intent_spec(raw: Any) -> IntentSpec:
    if isinstance(raw, IntentSpec):
        return raw
    if isinstance(raw, dict):
        spec = IntentSpec.from_dict(raw)
        intervention = raw.get("intervention") if isinstance(raw.get("intervention"), dict) else {}
        action = str(intervention.get("action") or raw.get("action") or "")
        if action == "batch_unit_quality" and not spec.scope.chapters:
            spec.kind = "batch_review"
            spec.coverage = "user_scope"
        elif action in ("pause",):
            spec.kind = "pause"
        elif action in ("edit_plot", "rewrite_outline"):
            spec.kind = "edit_plot"
        return spec
    return IntentSpec()


def intent_spec_from_payload(payload: dict[str, Any]) -> IntentSpec:
    raw = payload.get("intent_spec")
    if raw:
        return normalize_intent_spec(raw)
    intervention = payload.get("mission_intervention") or {}
    contract = payload.get("turn_contract") or {}
    primary = str(contract.get("primary_op") or "")
    if str(intervention.get("action") or "") == "batch_unit_quality" or primary == "batch_unit_quality":
        from app.services.mission.batch_unit_work_plan import _last_written_chapter

        last = _last_written_chapter({"mission": payload.get("mission"), "input_payload": payload})
        chapters = list(range(1, max(1, last) + 1)) if last else []
        return IntentSpec(
            kind="batch_review",
            scope=IntentScope(chapters=chapters, mode="score_and_fix"),
            coverage="user_scope",
        )
    return IntentSpec(kind="continue_write")


def persist_intent_spec(state: dict[str, Any], spec: IntentSpec) -> dict[str, Any]:
    payload = dict(state.get("input_payload") or {})
    payload["intent_spec"] = spec.to_dict()
    return {**state, "input_payload": payload}
