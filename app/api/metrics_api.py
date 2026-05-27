from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.api.deps import get_current_principal
from app.config.settings import settings
from app.services.auth_service import AuthPrincipal
from app.services.metrics_service import get_metrics_service
from app.services.tenant_context import get_tenant_id

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/summary")
def metrics_summary(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict:
    """Human-readable metrics summary for dashboard (§14.2)."""
    tenant_id = get_tenant_id() if settings.MULTI_TENANT_ENABLED else None
    return get_metrics_service().summary(tenant_id=tenant_id)


@router.get("/tenant")
def metrics_tenant_snapshot(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict:
    """Tenant quota + LLM cost snapshot (uses X-Tenant-Id when multi-tenant enabled)."""
    tid = get_tenant_id() or "default"
    return get_metrics_service().tenant_metrics_snapshot(tid)


@router.get("")
def prometheus_metrics() -> PlainTextResponse:
    """Prometheus scrape endpoint when metrics_enabled."""
    if not settings.METRICS_ENABLED:
        return PlainTextResponse("", status_code=404)
    body = get_metrics_service().prometheus_text()
    return PlainTextResponse(body or "# no metrics\n", media_type="text/plain; version=0.0.4")
