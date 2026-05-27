from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config.settings import settings
from app.services.auth_service import get_auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    role: str


@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    if not settings.AUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Authentication is disabled")
    principal = get_auth_service().authenticate_user(request.username, request.password)
    if not principal:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = get_auth_service().create_access_token(principal)
    return LoginResponse(
        access_token=token,
        user_id=principal.user_id,
        role=principal.role,
    )
