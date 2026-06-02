"""Tenant provisioning and quota API (Batch 4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_role
from app.api.tenant_access import assert_tenant_access
from app.config.settings import settings
from app.services.auth_service import AuthPrincipal
from app.services.metrics_service import get_metrics_service
from app.services.tenant_context import get_tenant_id
from app.services.tenant_storage import (
    create_tenant_storage,
    drop_tenant_storage,
    sanitize_tenant_id,
)

router = APIRouter(prefix="/tenants", tags=["tenants"])


class CreateTenantRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)


class TenantStorageResponse(BaseModel):
    tenant_id: str
    backend: str
    detail: dict[str, str]


def _assert_multi_tenant_enabled() -> None:
    if not settings.MULTI_TENANT_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Multi-tenant mode is disabled (set tenant.enabled=true)",
        )


@router.post("", response_model=TenantStorageResponse, status_code=201)
def create_tenant(
    request: CreateTenantRequest,
    _principal: AuthPrincipal = Depends(require_role("admin")),
) -> TenantStorageResponse:
    """Provision isolated storage (SQLite file or PG schema) for a tenant."""
    _assert_multi_tenant_enabled()
    try:
        safe_id = sanitize_tenant_id(request.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        detail = create_tenant_storage(safe_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TenantStorageResponse(
        tenant_id=safe_id,
        backend=str(detail.get("backend", "")),
        detail={k: str(v) for k, v in detail.items() if k not in ("backend", "tenant_id")},
    )


@router.delete("/{tenant_id}", response_model=TenantStorageResponse)
def delete_tenant(
    tenant_id: str,
    _principal: AuthPrincipal = Depends(require_role("admin")),
) -> TenantStorageResponse:
    """Remove tenant storage (destructive)."""
    _assert_multi_tenant_enabled()
    try:
        safe_id = sanitize_tenant_id(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        detail = drop_tenant_storage(safe_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TenantStorageResponse(
        tenant_id=safe_id,
        backend=str(detail.get("backend", "")),
        detail={k: str(v) for k, v in detail.items() if k not in ("backend", "tenant_id")},
    )


@router.get("/{tenant_id}/quota")
def tenant_quota(
    tenant_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Quota usage, limits, and in-process LLM cost estimate for a tenant."""
    try:
        safe_id = sanitize_tenant_id(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    assert_tenant_access(principal, safe_id)
    return get_metrics_service().tenant_metrics_snapshot(safe_id)


@router.get("/current/quota")
def current_tenant_quota(
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Quota for the tenant in X-Tenant-Id (or default)."""
    tid = get_tenant_id() or "default"
    assert_tenant_access(principal, tid)
    return get_metrics_service().tenant_metrics_snapshot(tid)
