"""
Plan validation — semantic checks after planning, before routing.

Complements normalize_planning_plan / normalize_selected_tools (format only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.runtime.state import AgentState
from app.services.tool_registry import get_tool_registry

_VAGUE_KEYWORDS = frozenset(
    {"分析", "思考", "准备", "研究", "了解", "考虑", "analyze", "consider", "think about"}
)


@dataclass
class PlanValidationResult:
    valid: bool
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    should_replan: bool = False


def _step_is_vague(step: str) -> bool:
    text = (step or "").strip()
    if len(text) >= 15:
        return False
    lower = text.lower()
    return any(kw in lower for kw in _VAGUE_KEYWORDS)


def validate_plan(
    plan: list[str],
    selected_tools: list[str],
    payload: dict[str, Any],
    state: AgentState,
) -> PlanValidationResult:
    """Validate plan quality; return issues and whether to trigger replan."""
    issues: list[str] = []
    suggestions: list[str] = []

    steps = [str(s).strip() for s in (plan or []) if str(s).strip()]
    if not steps:
        issues.append("plan_empty: no executable steps")
        suggestions.append("replan with at least one concrete step")

    if steps:
        vague_count = sum(1 for step in steps if _step_is_vague(step))
        if len(steps) >= 2 and vague_count > len(steps) * 0.6:
            issues.append("plan_too_vague: >60% steps are vague")
            suggestions.append("replan with concrete actions")

    registry = get_tool_registry()
    for tool in selected_tools or []:
        name = str(tool).strip()
        if not name:
            continue
        try:
            registry.get(name)
        except KeyError:
            issues.append(f"unknown_tool: {name}")
            suggestions.append(f"remove or replace unknown tool '{name}'")

    mission = state.get("mission") or payload.get("mission") or {}
    contract = payload.get("turn_contract") or {}
    forbid = {str(x).lower() for x in (contract.get("forbid") or [])}
    if forbid:
        for step in steps:
            lower = step.lower()
            for forbidden in forbid:
                if forbidden and forbidden in lower:
                    issues.append(
                        f"plan_conflicts_contract: step '{step}' vs forbid '{forbidden}'"
                    )

    budget = (mission.get("budget") or {}).get("max_steps")
    if budget and len(steps) > int(budget):
        issues.append(f"plan_exceeds_budget: {len(steps)} > {budget}")

    risk_level = str(payload.get("risk_level") or "LOW").upper()
    for tool in selected_tools or []:
        try:
            spec = registry.get(str(tool))
        except KeyError:
            continue
        tool_risk = str(spec.risk_level or "LOW").upper()
        if risk_level in ("LOW", "MEDIUM") and tool_risk in ("HIGH", "CRITICAL"):
            issues.append(f"risk_mismatch: {tool} is {tool_risk} but task risk is {risk_level}")

    should_replan = any(
        tag in issue
        for issue in issues
        for tag in ("plan_too_vague", "unknown_tool", "plan_empty")
    )
    return PlanValidationResult(
        valid=not issues,
        issues=issues,
        suggestions=suggestions,
        should_replan=should_replan,
    )
