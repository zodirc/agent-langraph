from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PolicyDecision:
    result: str
    reason: str
    risk_level: str
    metadata: Optional[dict[str, Any]] = None


@dataclass
class ReflectionVerdict:
    """Structured recoverable critique — drives routing, not just narrative."""

    failure_type: str
    root_cause: str
    recoverable: bool
    recommended_action: str
    required_facts: list[str] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_type": self.failure_type,
            "root_cause": self.root_cause,
            "recoverable": self.recoverable,
            "recommended_action": self.recommended_action,
            "required_facts": list(self.required_facts),
            "confidence": self.confidence,
        }


def build_reflection_verdict(state: dict[str, Any], reflection: dict[str, Any]) -> ReflectionVerdict:
    """Derive executable verdict from reflection output and turn evidence."""
    issues = list(reflection.get("issues") or [])
    critique = str(reflection.get("critique") or "")
    text = " ".join(issues + ([critique] if critique else [])).lower()

    failure_type = "none"
    recommended = "proceed"
    recoverable = True
    confidence = 0.7

    if any("plan_rejected" in i or "route_audit" in i for i in issues):
        failure_type = "plan_vague"
        recommended = "replan"
        confidence = 0.85
    elif any("tool_blocked" in i or "permission_denied" in i for i in issues):
        failure_type = "tool_error"
        recommended = "replan"
        confidence = 0.8
    elif any("turn_contract" in i or "contract" in i for i in issues):
        failure_type = "contract_violated"
        recommended = "replan"
        confidence = 0.82
    elif reflection.get("retry_planning"):
        failure_type = "plan_vague"
        recommended = "replan"
    elif reflection.get("retry_reasoning"):
        failure_type = "reasoning_quality"
        recommended = "retry_same_step"
    elif any("fact_warning" in i or "future writing" in text for i in issues):
        failure_type = "artifact_incomplete"
        recommended = "retry_same_step"
        confidence = 0.75
    elif "low confidence" in text:
        failure_type = "reasoning_quality"
        recommended = "retry_same_step"
        confidence = 0.6

    if any("human" in i or "review" in i for i in issues) and failure_type != "none":
        recommended = "ask_human"
        recoverable = False

    if not issues and not reflection.get("retry_planning") and not reflection.get("retry_reasoning"):
        failure_type = "none"
        recommended = "proceed"
        recoverable = True
        confidence = 0.9

    root_cause = critique or (issues[0] if issues else "no issues detected")
    required_facts: list[str] = []
    turn_facts = state.get("turn_facts") or {}
    if recommended == "replan":
        required_facts.append("turn_facts.plan")
        required_facts.append("route_audit.issues")
    elif recommended == "retry_same_step":
        required_facts.append("turn_facts.executed_actions")

    if turn_facts.get("has_failures"):
        failure_type = failure_type if failure_type != "none" else "execution_failed"
        if recommended == "proceed":
            recommended = "degrade"

    return ReflectionVerdict(
        failure_type=failure_type,
        root_cause=root_cause[:500],
        recoverable=recoverable,
        recommended_action=recommended,
        required_facts=required_facts,
        confidence=confidence,
    )


def apply_verdict_to_reflection(reflection: dict[str, Any], verdict: ReflectionVerdict) -> dict[str, Any]:
    """Sync legacy retry flags with structured verdict."""
    action = verdict.recommended_action
    return {
        **reflection,
        "verdict": verdict.to_dict(),
        "retry_planning": action == "replan",
        "retry_reasoning": action == "retry_same_step",
        "ask_human": action == "ask_human",
        "degrade": action == "degrade",
    }
