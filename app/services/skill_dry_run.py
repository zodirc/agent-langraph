"""Dry-run skill resolution and planning preview (no graph execution)."""

from __future__ import annotations

from typing import Any, Optional

from app.config.prompts import build_planning_system_prompt
from app.services.skill_resolver import resolve_skill_for_task
from app.services.skill_task_attach import merge_domain_pack_tools_with_skill, skill_tool_allowlist_from_state
from app.services.tool_registry import get_tool_registry
from app.services.tool_selection import format_tools_for_prompt, retrieve_relevant_tools


def dry_run_skill(
    skill_id: str,
    *,
    goal: str = "",
    user_role: str = "user",
    tenant_id: Optional[str] = None,
    skill_params: Optional[dict[str, Any]] = None,
    domain: Optional[str] = None,
) -> dict[str, Any]:
    definition, policy, snapshot = resolve_skill_for_task(
        skill_id,
        user_role=user_role,
        tenant_id=tenant_id,
        skill_params=skill_params,
        domain_override=domain,
    )
    resolved_domain = policy.resolved_domain
    state: dict[str, Any] = {
        "task_type": "qa",
        "skill_id": skill_id,
        "skill_runtime_policy": policy.to_dict(),
        "skill_snapshot": snapshot,
        "input_payload": {
            "goal": goal,
            "risk_level": "LOW",
            **(skill_params or {}),
        },
    }
    allowlist = skill_tool_allowlist_from_state(state)
    registry = get_tool_registry()
    tools = retrieve_relevant_tools(
        goal,
        resolved_domain,
        "LOW",
        registry,
        top_k=12,
        pack_tools=allowlist,
        skill_blocklist=policy.resolved_tool_blocklist,
    )
    planning_prompt = build_planning_system_prompt(state)
    preview_chars = 4000
    return {
        "skill_id": skill_id,
        "definition_summary": {
            "name": definition.name,
            "version": definition.version,
            "status": definition.status.value,
            "base_domain": definition.base_domain,
        },
        "runtime_policy": policy.to_dict(),
        "visible_tools": format_tools_for_prompt(tools),
        "tool_count": len(tools),
        "planning_prompt_preview": planning_prompt[:preview_chars],
        "planning_prompt_truncated": len(planning_prompt) > preview_chars,
        "resolved_domain": resolved_domain,
        "execution_mode_hint": policy.resolved_execution_mode_hint,
    }
