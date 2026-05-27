from __future__ import annotations

from dataclasses import dataclass
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


class AuthService:
    def authenticate_user(self, username: str, password: str) -> Optional[AuthPrincipal]:
        for user in settings.AUTH_USERS:
            if user["username"] == username and user["password"] == password:
                return AuthPrincipal(
                    user_id=user["user_id"],
                    role=user["role"],
                    auth_method="jwt",
                    username=username,
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
        return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm="HS256")

    def decode_access_token(self, token: str) -> Optional[AuthPrincipal]:
        try:
            payload = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=["HS256"])
            return AuthPrincipal(
                user_id=str(payload.get("sub", "anonymous")),
                role=str(payload.get("role", "user")),
                auth_method="jwt",
                username=payload.get("username"),
            )
        except jwt.PyJWTError:
            return None

    def authenticate_api_key(self, api_key: str) -> Optional[AuthPrincipal]:
        for entry in settings.AUTH_API_KEYS:
            if entry["key"] and entry["key"] == api_key:
                return AuthPrincipal(
                    user_id=entry["user_id"],
                    role=entry["role"],
                    auth_method="api_key",
                )
        return None


_service: AuthService | None = None


def get_auth_service() -> AuthService:
    global _service
    if _service is None:
        _service = AuthService()
    return _service
