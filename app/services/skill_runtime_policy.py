"""Build SkillRuntimePolicy from SkillDefinition and task context."""

from __future__ import annotations

from typing import Any, Optional

from app.domain.skill_models import SkillDefinition, SkillRuntimePolicy
from app.services.tool_registry import get_tool_registry


def build_runtime_policy(
    definition: SkillDefinition,
    *,
    skill_params: Optional[dict[str, Any]] = None,
    domain_override: Optional[str] = None,
) -> SkillRuntimePolicy:
    _ = skill_params  # reserved for overlay templating in later phases
    domain = (domain_override or definition.base_domain or "single_turn").lower()
    allowlist = list(definition.allowed_tools or [])
    blocklist = list(definition.blocked_tools or [])
    if allowlist:
        registry = get_tool_registry()
        known = set(registry.list_tools())
        allowlist = [t for t in allowlist if t in known]

    action_weights: dict[str, float] = {}
    raw_policy = definition.action_policy or {}
    weights = raw_policy.get("weights") if isinstance(raw_policy, dict) else None
    if isinstance(weights, dict):
        for key, val in weights.items():
            try:
                action_weights[str(key)] = float(val)
            except (TypeError, ValueError):
                continue

    hooks: list[str] = []
    if definition.plugin_ref:
        hooks.append(definition.plugin_ref)

    from app.services.skill_overlay_template import render_skill_text

    params = dict(skill_params or {})
    if "goal" not in params and params.get("query"):
        params["goal"] = params["query"]
    policy = SkillRuntimePolicy(
        skill_id=definition.skill_id,
        version=definition.version,
        resolved_domain=domain,
        resolved_execution_mode_hint=definition.preferred_execution_mode or "single",
        resolved_tool_allowlist=allowlist,
        resolved_tool_blocklist=blocklist,
        resolved_planning_overlay=render_skill_text(definition.planning_overlay or "", params).strip(),
        resolved_reasoning_overlay=render_skill_text(definition.reasoning_overlay or "", params).strip(),
        resolved_reflection_overlay=render_skill_text(definition.reflection_overlay or "", params).strip(),
        resolved_action_weights=action_weights,
        resolved_output_contract=dict(definition.output_contract or {}),
        resolved_plugin_hooks=hooks,
        source_type=definition.source_type.value,
        owner_type=definition.owner_type.value,
        owner_id=definition.owner_id,
    )
    from app.services.skill_hooks import (
        HOOK_TYPE_PLANNING,
        HOOK_TYPE_TOOL_FILTER,
        apply_trusted_hooks,
    )

    ctx = {"skill_id": definition.skill_id, "skill_params": skill_params or {}}
    patched = apply_trusted_hooks(policy.to_dict(), context=ctx, hook_types=[HOOK_TYPE_PLANNING])
    patched = apply_trusted_hooks(patched, context=ctx, hook_types=[HOOK_TYPE_TOOL_FILTER])
    return SkillRuntimePolicy.model_validate(patched)
