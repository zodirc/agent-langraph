"""Resolve skill_id to SkillRuntimePolicy with visibility and permission checks."""

from __future__ import annotations

from typing import Any, Optional

from app.domain.skill_models import SkillDefinition, SkillRuntimePolicy, SkillVisibility
from app.services.skill_registry import get_skill_registry
from app.services.skill_runtime_policy import build_runtime_policy


class SkillResolveError(Exception):
    """Base error for skill resolution."""

    def __init__(self, message: str, *, code: str = "skill_error") -> None:
        super().__init__(message)
        self.code = code


class SkillNotFoundError(SkillResolveError):
    def __init__(self, skill_id: str) -> None:
        super().__init__(f"Unknown skill: {skill_id}", code="not_found")


class SkillNotAvailableError(SkillResolveError):
    def __init__(self, skill_id: str, reason: str) -> None:
        super().__init__(f"Skill '{skill_id}' unavailable: {reason}", code="unavailable")


class SkillPermissionError(SkillResolveError):
    def __init__(self, skill_id: str) -> None:
        super().__init__(f"Permission denied for skill: {skill_id}", code="forbidden")


def resolve_skill_for_task(
    skill_id: str,
    *,
    user_role: str = "user",
    tenant_id: Optional[str] = None,
    skill_params: Optional[dict[str, Any]] = None,
    domain_override: Optional[str] = None,
) -> tuple[SkillDefinition, SkillRuntimePolicy, dict[str, Any]]:
    from app.services.skill_governance import is_skill_blocked_for_tenant

    if is_skill_blocked_for_tenant(skill_id, tenant_id):
        raise SkillNotAvailableError(skill_id, "blocked by catalog governance")

    registry = get_skill_registry()
    try:
        definition = registry.load_definition(skill_id, tenant_id=tenant_id)
    except KeyError as exc:
        raise SkillNotFoundError(skill_id) from exc

    if not definition.is_usable_by_task():
        raise SkillNotAvailableError(skill_id, f"status={definition.status.value}")

    if not registry.role_allows(definition.required_role, user_role):
        raise SkillPermissionError(skill_id)

    if definition.visibility in (SkillVisibility.TENANT_PRIVATE, SkillVisibility.TENANT_SHARED):
        if definition.source_type.value == "tenant" and tenant_id != definition.owner_id:
            raise SkillPermissionError(skill_id)

    policy = build_runtime_policy(
        definition,
        skill_params=skill_params,
        domain_override=domain_override,
    )
    snapshot = definition.to_snapshot()
    snapshot["skill_params"] = dict(skill_params or {})
    return definition, policy, snapshot


def attach_skill_to_payload(
    payload: dict[str, Any],
    *,
    skill_id: str,
    skill_params: Optional[dict[str, Any]] = None,
    user_role: str = "user",
    tenant_id: Optional[str] = None,
) -> dict[str, Any]:
    """Merge skill resolution into task input_payload (structured only)."""
    definition, policy, snapshot = resolve_skill_for_task(
        skill_id,
        user_role=user_role,
        tenant_id=tenant_id,
        skill_params=skill_params,
    )
    out = dict(payload)
    params = dict(skill_params or {})
    if params:
        out.setdefault("skill_params", {}).update(params)
    out["skill_id"] = definition.skill_id
    if definition.preferred_execution_mode and "execution_mode" not in out:
        out["execution_mode"] = definition.preferred_execution_mode
    if definition.base_domain and definition.base_domain != "single_turn":
        out.setdefault("domain", definition.base_domain)
    out["_skill_policy"] = policy.to_dict()
    out["_skill_snapshot"] = snapshot
    return out
