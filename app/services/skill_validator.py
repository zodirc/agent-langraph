"""Static validation for skill definitions before load/publish."""

from __future__ import annotations

from typing import Any

from app.domain.skill_models import SkillDefinition

_MAX_OVERLAY_CHARS = 16_000


def validate_skill_definition(
    definition: SkillDefinition,
    *,
    known_tools: list[str] | None = None,
) -> list[str]:
    """Return list of validation error messages (empty if valid)."""
    issues: list[str] = []
    if not definition.skill_id or not definition.skill_id.strip():
        issues.append("skill_id is required")
    if not definition.name or not definition.name.strip():
        issues.append("name is required")
    for field_name in ("planning_overlay", "reasoning_overlay", "reflection_overlay"):
        text = getattr(definition, field_name, "") or ""
        if len(text) > _MAX_OVERLAY_CHARS:
            issues.append(f"{field_name} exceeds {_MAX_OVERLAY_CHARS} characters")
    if known_tools is not None:
        known = set(known_tools)
        for tool in definition.allowed_tools or []:
            if tool not in known:
                issues.append(f"allowed_tools: unknown tool '{tool}'")
        for tool in definition.blocked_tools or []:
            if tool not in known:
                issues.append(f"blocked_tools: unknown tool '{tool}'")
    contract = definition.output_contract or {}
    if contract and not isinstance(contract, dict):
        issues.append("output_contract must be an object")
    return issues


def validate_skill_dict(data: dict[str, Any], *, known_tools: list[str] | None = None) -> list[str]:
    try:
        definition = SkillDefinition.model_validate(data)
    except Exception as exc:
        return [f"schema: {exc}"]
    return validate_skill_definition(definition, known_tools=known_tools)
