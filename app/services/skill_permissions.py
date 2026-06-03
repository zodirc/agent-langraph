"""Skill management permissions (Phase 2)."""

from __future__ import annotations

from fastapi import HTTPException

from app.services.auth_service import AuthPrincipal

_ROLE_ORDER = {"guest": 0, "user": 1, "admin": 2}

_SKILL_PERMISSIONS: dict[str, set[str]] = {
    "skill.read": {"guest", "user", "admin"},
    "skill.create": {"user", "admin"},
    "skill.update": {"user", "admin"},
    "skill.publish": {"user", "admin"},
    "skill.disable": {"user", "admin"},
    "skill.delete": {"admin"},
    "skill.clone": {"user", "admin"},
    "skill.rollback": {"user", "admin"},
}


def role_at_least(role: str, minimum: str) -> bool:
    return _ROLE_ORDER.get(role, 0) >= _ROLE_ORDER.get(minimum, 0)


def has_skill_permission(principal: AuthPrincipal, permission: str) -> bool:
    allowed = _SKILL_PERMISSIONS.get(permission, set())
    return principal.role in allowed


def require_skill_permission(principal: AuthPrincipal, permission: str) -> None:
    if not has_skill_permission(principal, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Missing permission: {permission}",
        )


def can_manage_definition(principal: AuthPrincipal, owner_id: str, owner_type: str) -> bool:
    if principal.role == "admin":
        return True
    if owner_type == "user":
        return principal.user_id == owner_id
    if owner_type == "tenant":
        tid = principal.tenant_id or ""
        return bool(tid) and tid == owner_id
    return False
