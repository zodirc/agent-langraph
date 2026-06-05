"""Structured intent observation result (pre-planning layer)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

IntentKind = Literal["qa", "engineering", "writing", "mission_control"]
TargetMode = Literal["qa_mode", "engineering_mode", "manuscript_mode"]
SessionRelation = Literal["stay", "switch", "isolate"]
TurnIntentClass = Literal[
    "narrate_only",
    "steer_replan",
    "steer_execute",
    "mission_step_execute",
    "mechanical_continue",
]
ObservationSource = Literal["llm", "explicit", "structural", "hybrid"]


@dataclass
class IntentObservationResult:
    version: str = "v1"
    source: str = "structural"
    intent_kind: str = "qa"
    target_mode: str = "qa_mode"
    session_relation: str = "stay"
    turn_kind_candidate: str | None = None
    needs_planning: bool = True
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    trace_id: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    fallback_used: bool = False
    shadow_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "source": self.source,
            "intent_kind": self.intent_kind,
            "target_mode": self.target_mode,
            "session_relation": self.session_relation,
            "needs_planning": self.needs_planning,
            "confidence": round(float(self.confidence), 4),
            "reasons": list(self.reasons),
            "trace_id": self.trace_id,
        }
        if self.turn_kind_candidate:
            out["turn_kind_candidate"] = self.turn_kind_candidate
        if self.model_name:
            out["model_name"] = self.model_name
        if self.latency_ms is not None:
            out["latency_ms"] = self.latency_ms
        if self.fallback_used:
            out["fallback_used"] = True
        if self.shadow_only:
            out["shadow_only"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntentObservationResult:
        if not isinstance(data, dict):
            return cls()
        return cls(
            version=str(data.get("version") or "v1"),
            source=str(data.get("source") or "structural"),
            intent_kind=str(data.get("intent_kind") or "qa"),
            target_mode=str(data.get("target_mode") or "qa_mode"),
            session_relation=str(data.get("session_relation") or "stay"),
            turn_kind_candidate=(
                str(data["turn_kind_candidate"])
                if data.get("turn_kind_candidate")
                else None
            ),
            needs_planning=bool(data.get("needs_planning", True)),
            confidence=float(data.get("confidence") or 0.0),
            reasons=[str(r) for r in (data.get("reasons") or [])],
            trace_id=str(data["trace_id"]) if data.get("trace_id") else None,
            model_name=str(data["model_name"]) if data.get("model_name") else None,
            latency_ms=int(data["latency_ms"]) if data.get("latency_ms") is not None else None,
            fallback_used=bool(data.get("fallback_used")),
            shadow_only=bool(data.get("shadow_only")),
        )


def new_observation_trace_id() -> str:
    return f"io-{uuid.uuid4().hex[:12]}"
