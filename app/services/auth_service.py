from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt

from app.config.settings import settings


@dataclass
class AuthPrincipal:
    user_id: str
    role: str
    auth_method: str
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    tenant_ids: list[str] = field(default_factory=list)


def _parse_tenant_ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


def _verify_password(plain: str, stored: str) -> bool:
    if stored.startswith("pbkdf2:"):
        try:
            _, iterations_s, salt, digest = stored.split(":", 3)
            iterations = int(iterations_s)
            computed = hashlib.pbkdf2_hmac(
                "sha256",
                plain.encode("utf-8"),
                salt.encode("utf-8"),
                iterations,
            ).hex()
            return hmac.compare_digest(computed, digest)
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(plain, stored)


def hash_password(plain: str, *, iterations: int = 260_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return f"pbkdf2:{iterations}:{salt}:{digest}"


class AuthService:
    def authenticate_user(self, username: str, password: str) -> Optional[AuthPrincipal]:
        for user in settings.AUTH_USERS:
            if user["username"] != username:
                continue
            stored = user.get("password_hash") or user.get("password", "")
            if not _verify_password(password, stored):
                continue
            tenant_ids = _parse_tenant_ids(user.get("tenant_ids"))
            tenant_id = str(user.get("tenant_id", "")).strip() or None
            if tenant_id and tenant_id not in tenant_ids:
                tenant_ids = [tenant_id, *tenant_ids]
            return AuthPrincipal(
                user_id=user["user_id"],
                role=user["role"],
                auth_method="jwt",
                username=username,
                tenant_id=tenant_id or (tenant_ids[0] if len(tenant_ids) == 1 else None),
                tenant_ids=tenant_ids,
            )
        return None

    def create_access_token(self, principal: AuthPrincipal) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
        payload: dict[str, Any] = {
            "sub": principal.user_id,
            "role": principal.role,
            "username": principal.username,
            "exp": expire,
            "iat": datetime.now(timezone.utc),
        }
        if principal.tenant_id:
            payload["tenant_id"] = principal.tenant_id
        if principal.tenant_ids:
            payload["tenant_ids"] = principal.tenant_ids
        return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm="HS256")

    def decode_access_token(self, token: str) -> Optional[AuthPrincipal]:
        try:
            payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])
            tenant_ids = _parse_tenant_ids(payload.get("tenant_ids"))
            tenant_id = str(payload.get("tenant_id", "")).strip() or None
            if tenant_id and tenant_id not in tenant_ids:
                tenant_ids = [tenant_id, *tenant_ids]
            return AuthPrincipal(
                user_id=str(payload.get("sub", "anonymous")),
                role=str(payload.get("role", "user")),
                auth_method="jwt",
                username=payload.get("username"),
                tenant_id=tenant_id or (tenant_ids[0] if len(tenant_ids) == 1 else None),
                tenant_ids=tenant_ids,
            )
        except jwt.PyJWTError:
            return None

    def authenticate_api_key(self, api_key: str) -> Optional[AuthPrincipal]:
        for entry in settings.AUTH_API_KEYS:
            if entry["key"] and entry["key"] == api_key:
                tenant_ids = _parse_tenant_ids(entry.get("tenant_ids"))
                tenant_id = str(entry.get("tenant_id", "")).strip() or None
                if tenant_id and tenant_id not in tenant_ids:
                    tenant_ids = [tenant_id, *tenant_ids]
                return AuthPrincipal(
                    user_id=entry["user_id"],
                    role=entry["role"],
                    auth_method="api_key",
                    tenant_id=tenant_id or (tenant_ids[0] if len(tenant_ids) == 1 else None),
                    tenant_ids=tenant_ids,
                )
        return None


_service: AuthService | None = None


def get_auth_service() -> AuthService:
    global _service
    if _service is None:
        _service = AuthService()
    return _service
