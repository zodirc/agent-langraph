"""Validate task output against skill output_contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

_CITATION_MARKERS = re.compile(
    r"\[[\d]+\]|\(source:|\bcitation\b|\b来源\b|\b引用\b",
    re.IGNORECASE,
)


@dataclass
class SkillOutputValidation:
    passed: bool
    issues: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": list(self.issues),
            "strict": self.strict,
        }


def _contract_from_state(state: dict[str, Any]) -> dict[str, Any]:
    policy = state.get("skill_runtime_policy") or {}
    if not isinstance(policy, dict):
        return {}
    contract = policy.get("resolved_output_contract") or {}
    return contract if isinstance(contract, dict) else {}


def validate_skill_output(state: dict[str, Any]) -> SkillOutputValidation:
    """Check final answer / structured output against skill contract."""
    skill_id = state.get("skill_id")
    if not skill_id:
        return SkillOutputValidation(passed=True)

    contract = dict(_contract_from_state(state))
    policy = dict(state.get("skill_runtime_policy") or {})
    if policy:
        from app.services.skill_hooks import HOOK_TYPE_OUTPUT_VALIDATE, apply_hook_stage

        patched = apply_hook_stage(
            policy,
            HOOK_TYPE_OUTPUT_VALIDATE,
            context={"skill_id": state.get("skill_id"), "state": state},
        )
        hook_rules = patched.get("output_validation_rules") or {}
        if isinstance(hook_rules, dict):
            contract.update(hook_rules)
    if not contract:
        return SkillOutputValidation(passed=True)

    issues: list[str] = []
    reasoning = state.get("reasoning_result") or {}
    summary = str(reasoning.get("summary") or state.get("final_answer") or "").strip()
    structured = state.get("structured_output")
    if structured is None and isinstance(reasoning.get("structured"), dict):
        structured = reasoning.get("structured")
    artifacts = state.get("artifacts") or []
    if isinstance(reasoning.get("structured"), dict):
        art_from_reasoning = (reasoning["structured"] or {}).get("artifacts")
        if art_from_reasoning and not artifacts:
            artifacts = art_from_reasoning

    min_summary = int(contract.get("min_summary_chars") or 20)
    if contract.get("require_summary") and len(summary) < min_summary:
        issues.append("skill_contract: missing or too short summary")

    if contract.get("prefer_structured") or contract.get("require_structured"):
        if not structured:
            issues.append("skill_contract: structured_output required")
        elif contract.get("require_structured"):
            if isinstance(structured, dict) and not structured:
                issues.append("skill_contract: empty structured_output")

    if contract.get("require_artifacts"):
        if not artifacts:
            issues.append("skill_contract: artifacts required")

    if contract.get("require_citations"):
        text = summary + json.dumps(structured or {}, ensure_ascii=False)
        if not _CITATION_MARKERS.search(text):
            issues.append("skill_contract: citations required")

    if contract.get("require_action_items"):
        if not re.search(r"action\s*item|下一步|待办", summary, re.IGNORECASE):
            issues.append("skill_contract: action items expected in summary")

    strict = bool(contract.get("strict") or contract.get("block_on_fail"))
    passed = not issues
    return SkillOutputValidation(passed=passed, issues=issues, strict=strict)
