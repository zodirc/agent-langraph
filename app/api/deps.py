from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import app.config.settings as settings_module

# Backward-compatible alias for tests/older imports.
settings = settings_module.settings
from app.api.tenant_access import assert_task_access, bind_tenant_for_principal
from app.services.auth_service import AuthPrincipal, get_auth_service
from app.services.rate_limit import enforce_rate_limits
from app.services.tenant_context import apply_tenant_headers

_bearer = HTTPBearer(auto_error=False)


def _authenticate_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
) -> AuthPrincipal:
    if not settings_module.settings.AUTH_ENABLED:
        header_user = request.headers.get("X-User-Id")
        header_tenant = request.headers.get("X-Tenant-Id") or request.headers.get("x-tenant-id")
        tid = str(header_tenant).strip() if header_tenant else None
        return AuthPrincipal(
            user_id=header_user or "anonymous",
            role=request.headers.get("X-User-Role", "user"),
            auth_method="anonymous",
            tenant_id=tid,
        )

    auth = get_auth_service()
    api_key = request.headers.get("X-API-Key")
    if api_key:
        principal = auth.authenticate_api_key(api_key)
        if principal:
            return principal
        raise HTTPException(status_code=401, detail="Invalid API key")

    if credentials and credentials.credentials:
        principal = auth.decode_access_token(credentials.credentials)
        if principal:
            return principal
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Use Bearer JWT or X-API-Key header.",
    )


def get_current_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthPrincipal:
    apply_tenant_headers(dict(request.headers))
    principal = _authenticate_principal(request, credentials)
    enforce_rate_limits(request, principal.user_id)
    if settings_module.settings.MULTI_TENANT_ENABLED:
        bind_tenant_for_principal(request, principal)
    return principal


def get_current_tenant_principal(
    principal: AuthPrincipal = Depends(get_current_principal),
) -> AuthPrincipal:
    """Principal with tenant context already validated (multi-tenant routes)."""
    return principal


def require_task_access_dep(
    task_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> AuthPrincipal:
    """FastAPI dependency: enforce task ownership for path ``task_id``."""
    assert_task_access(principal, task_id)
    return principal


def require_role(*roles: str):
    def checker(principal: AuthPrincipal = Depends(get_current_principal)) -> AuthPrincipal:
        if principal.role not in roles and principal.role != "admin":
            raise HTTPException(status_code=403, detail=f"Role '{principal.role}' not permitted")
        return principal

    return checker
