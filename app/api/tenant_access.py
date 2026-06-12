"""Tenant and resource access checks shared across API routes."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request

import app.config.settings as settings_module
from app.services.auth_service import AuthPrincipal
from app.services.state_store import get_state_store
from app.services.tenant_context import get_tenant_id, set_tenant_id


def principal_allowed_tenants(principal: AuthPrincipal) -> set[str] | None:
    """
    Tenants the principal may access.

    Returns None when any tenant is allowed (admin or auth-disabled dev mode).
    """
    if principal.role == "admin":
        return None
    if principal.tenant_ids:
        return {t for t in principal.tenant_ids if t}
    if principal.tenant_id:
        return {principal.tenant_id}
    return set()


def resolve_request_tenant_id(request: Request, principal: AuthPrincipal) -> str:
    """Resolve effective tenant from header and principal; set ContextVar."""
    settings = settings_module.settings
    header_tid = request.headers.get("X-Tenant-Id") or request.headers.get("x-tenant-id")
    header_tid = (header_tid or "").strip() or None

    if not settings.MULTI_TENANT_ENABLED:
        tid = header_tid or principal.tenant_id or "default"
        set_tenant_id(tid)
        return tid

    allowed = principal_allowed_tenants(principal)

    if header_tid:
        if allowed is not None and header_tid not in allowed:
            raise HTTPException(
                status_code=403,
                detail="Cannot access another tenant's resources",
            )
        set_tenant_id(header_tid)
        return header_tid

    if principal.tenant_id:
        set_tenant_id(principal.tenant_id)
        return principal.tenant_id

    if settings.AUTH_ENABLED and allowed is not None and len(allowed) == 1:
        only = next(iter(allowed))
        set_tenant_id(only)
        return only

    if settings.AUTH_ENABLED and principal.auth_method not in ("anonymous",):
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-Id header required when multi-tenant is enabled",
        )

    tid = "default"
    set_tenant_id(tid)
    return tid


def assert_tenant_access(principal: AuthPrincipal, tenant_id: str) -> None:
    """Verify principal may access a specific tenant id (path/query)."""
    if not settings_module.settings.MULTI_TENANT_ENABLED:
        return
    allowed = principal_allowed_tenants(principal)
    if allowed is None:
        return
    safe = tenant_id or "default"
    if safe not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Cannot access another tenant's resources",
        )
    current = get_tenant_id()
    if current and current != safe:
        raise HTTPException(
            status_code=403,
            detail="Tenant context does not match requested tenant",
        )


def assert_task_access(principal: AuthPrincipal, task_id: str) -> None:
    """Object-level check: task must exist and belong to the current user (non-admin)."""
    state = get_state_store().load(task_id, read_only=True)
    if not state:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if principal.role == "admin":
        return
    if not settings_module.settings.AUTH_ENABLED:
        return
    owner = str(state.get("user_id") or "")
    if owner and owner != principal.user_id:
        raise HTTPException(
            status_code=403,
            detail="Task does not belong to the current user",
        )


def assert_session_knowledge_access(principal: AuthPrincipal, session_id: str) -> None:
    """
    Session source KB may be saved before the first task message (Web pre-stages material).

    When the task row exists, enforce the same ownership rules as assert_task_access.
    Otherwise require an authenticated principal (require_role on the route).
    """
    state = get_state_store().load(session_id, read_only=True)
    if not state:
        return
    if principal.role == "admin":
        return
    if not settings_module.settings.AUTH_ENABLED:
        return
    owner = str(state.get("user_id") or "")
    if owner and owner != principal.user_id:
        raise HTTPException(
            status_code=403,
            detail="Task does not belong to the current user",
        )


def bind_tenant_for_principal(
    request: Request,
    principal: AuthPrincipal,
) -> AuthPrincipal:
    """Bind tenant context and validate principal ↔ tenant binding."""
    resolve_request_tenant_id(request, principal)
    return principal
