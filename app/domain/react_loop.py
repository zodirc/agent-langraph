"""
Self-Routed Deliberation Loop (SRDL) domain models.

Bounded ReAct loop for single runtime — controlled self-routing within explicit graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# Phase 1 actions (always available when loop enabled)
PHASE1_ACTIONS = frozenset(
    {"retrieve_knowledge", "call_tool", "reason", "finish"}
)
# Phase 2 extensions
PHASE2_ACTIONS = frozenset({"retrieve_memory", "replan"})
ALL_REACT_ACTIONS = PHASE1_ACTIONS | PHASE2_ACTIONS

ReactAction = Literal[
    "retrieve_knowledge",
    "retrieve_memory",
    "call_tool",
    "reason",
    "replan",
    "finish",
]

EXIT_PATHS = frozenset(
    {
        "finish_with_answer",
        "finish_with_degraded_answer",
        "back_to_planning",
        "reflection",
        "human_review",
        "dead_letter",
    }
)

RUNTIME_RECOMMENDATIONS = frozenset({"mission", "supervisor", "exploration"})


@dataclass
class ReactTraceItem:
    step: int
    thought_summary: str
    action: str
    action_input: dict[str, Any]
    observation_summary: str
    confidence: float
    continue_loop: bool
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "thought_summary": self.thought_summary,
            "action": self.action,
            "action_input": dict(self.action_input),
            "observation_summary": self.observation_summary,
            "confidence": self.confidence,
            "continue_loop": self.continue_loop,
            "why": self.why,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReactTraceItem:
        return cls(
            step=int(data.get("step") or 0),
            thought_summary=str(data.get("thought_summary") or ""),
            action=str(data.get("action") or ""),
            action_input=dict(data.get("action_input") or {}),
            observation_summary=str(data.get("observation_summary") or ""),
            confidence=float(data.get("confidence") or 0.0),
            continue_loop=bool(data.get("continue_loop", False)),
            why=str(data.get("why") or ""),
        )


@dataclass
class RouteRecommendation:
    suggested_runtime: str
    reason: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggested_runtime": self.suggested_runtime,
            "reason": self.reason,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RouteRecommendation | None:
        if not isinstance(data, dict) or not data.get("suggested_runtime"):
            return None
        return cls(
            suggested_runtime=str(data.get("suggested_runtime") or ""),
            reason=str(data.get("reason") or ""),
            confidence=float(data.get("confidence") or 0.0),
        )


@dataclass
class ReactDecision:
    thought_summary: str
    action: str
    action_input: dict[str, Any]
    continue_loop: bool
    why: str
    confidence: float
    route_recommendation: RouteRecommendation | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "thought_summary": self.thought_summary,
            "action": self.action,
            "action_input": dict(self.action_input),
            "continue_loop": self.continue_loop,
            "why": self.why,
            "confidence": self.confidence,
        }
        if self.route_recommendation:
            out["route_recommendation"] = self.route_recommendation.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReactDecision:
        rec_raw = data.get("route_recommendation")
        return cls(
            thought_summary=str(data.get("thought_summary") or ""),
            action=str(data.get("action") or "finish"),
            action_input=dict(data.get("action_input") or {}),
            continue_loop=bool(data.get("continue_loop", False)),
            why=str(data.get("why") or ""),
            confidence=float(data.get("confidence") or 0.0),
            route_recommendation=RouteRecommendation.from_dict(rec_raw)
            if isinstance(rec_raw, dict)
            else None,
        )


@dataclass
class ReactLoopState:
    enabled: bool = False
    mode: str = "bounded"
    goal: str = ""
    step_index: int = 0
    max_steps: int = 4
    status: str = "idle"  # idle | running | finished | aborted
    allowed_actions: list[str] = field(default_factory=list)
    history: list[ReactTraceItem] = field(default_factory=list)
    exit_reason: str | None = None
    exit_path: str | None = None
    current_decision: dict[str, Any] | None = None
    replan_count: int = 0
    route_recommendation: dict[str, Any] | None = None
    pending_runtime_upgrade: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "goal": self.goal,
            "step_index": self.step_index,
            "max_steps": self.max_steps,
            "status": self.status,
            "allowed_actions": list(self.allowed_actions),
            "history": [h.to_dict() for h in self.history],
            "exit_reason": self.exit_reason,
            "exit_path": self.exit_path,
            "current_decision": self.current_decision,
            "replan_count": self.replan_count,
            "route_recommendation": self.route_recommendation,
            "pending_runtime_upgrade": self.pending_runtime_upgrade,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ReactLoopState:
        if not isinstance(data, dict):
            return cls()
        history = [
            ReactTraceItem.from_dict(h)
            for h in (data.get("history") or [])
            if isinstance(h, dict)
        ]
        return cls(
            enabled=bool(data.get("enabled", False)),
            mode=str(data.get("mode") or "bounded"),
            goal=str(data.get("goal") or ""),
            step_index=int(data.get("step_index") or 0),
            max_steps=int(data.get("max_steps") or 4),
            status=str(data.get("status") or "idle"),
            allowed_actions=[str(a) for a in (data.get("allowed_actions") or [])],
            history=history,
            exit_reason=data.get("exit_reason"),
            exit_path=data.get("exit_path"),
            current_decision=data.get("current_decision"),
            replan_count=int(data.get("replan_count") or 0),
            route_recommendation=data.get("route_recommendation"),
            pending_runtime_upgrade=data.get("pending_runtime_upgrade"),
        )


def get_react_loop(state: dict[str, Any]) -> ReactLoopState:
    return ReactLoopState.from_dict(state.get("react_loop"))


def merge_react_loop(state: dict[str, Any], loop: ReactLoopState) -> dict[str, Any]:
    from app.runtime.state import merge_state

    return merge_state(state, react_loop=loop.to_dict())
