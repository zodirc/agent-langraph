from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import settings
from app.services.auth_service import AuthPrincipal, get_auth_service
from app.services.rate_limit import enforce_rate_limits
from app.services.tenant_context import apply_tenant_headers, set_tenant_id

_bearer = HTTPBearer(auto_error=False)


def get_current_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthPrincipal:
    apply_tenant_headers(dict(request.headers))
    if settings.MULTI_TENANT_ENABLED:
        tid = request.headers.get("X-Tenant-Id")
        if tid:
            set_tenant_id(tid)

    if not settings.AUTH_ENABLED:
        header_user = request.headers.get("X-User-Id")
        principal = AuthPrincipal(
            user_id=header_user or "anonymous",
            role=request.headers.get("X-User-Role", "user"),
            auth_method="anonymous",
        )
        enforce_rate_limits(request, principal.user_id)
        return principal

    auth = get_auth_service()
    api_key = request.headers.get("X-API-Key")
    if api_key:
        principal = auth.authenticate_api_key(api_key)
        if principal:
            enforce_rate_limits(request, principal.user_id)
            return principal
        raise HTTPException(status_code=401, detail="Invalid API key")

    if credentials and credentials.credentials:
        principal = auth.decode_access_token(credentials.credentials)
        if principal:
            enforce_rate_limits(request, principal.user_id)
            return principal
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Use Bearer JWT or X-API-Key header.",
    )


def require_role(*roles: str):
    def checker(principal: AuthPrincipal = Depends(get_current_principal)) -> AuthPrincipal:
        if principal.role not in roles and principal.role != "admin":
            raise HTTPException(status_code=403, detail=f"Role '{principal.role}' not permitted")
        return principal

    return checker
