"""Skill catalog governance API (Phase 4 ops)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal, require_role
from app.api.skills_api import _require_skill_platform
from app.services.auth_service import AuthPrincipal
from app.services.skill_governance import (
    get_governance_snapshot,
    set_global_disabled,
    set_tenant_blocks,
)

router = APIRouter(prefix="/skills/admin", tags=["skills-admin"])


class GovernanceSkillIds(BaseModel):
    skill_ids: list[str] = Field(default_factory=list)
    reason: str = ""


@router.get("/governance")
def read_governance(
    _principal: AuthPrincipal = Depends(require_role("admin")),
) -> dict[str, Any]:
    _require_skill_platform()
    return get_governance_snapshot()


@router.put("/global-disabled")
def update_global_disabled(
    body: GovernanceSkillIds,
    principal: AuthPrincipal = Depends(require_role("admin")),
) -> dict[str, Any]:
    _require_skill_platform()
    return set_global_disabled(
        body.skill_ids,
        reason=body.reason,
        operator=principal.user_id,
    )


@router.put("/tenants/{tenant_id}/blocks")
def update_tenant_blocks(
    tenant_id: str,
    body: GovernanceSkillIds,
    principal: AuthPrincipal = Depends(require_role("admin")),
) -> dict[str, Any]:
    _require_skill_platform()
    if not tenant_id.strip():
        raise HTTPException(status_code=400, detail="tenant_id required")
    return set_tenant_blocks(
        tenant_id,
        body.skill_ids,
        reason=body.reason,
        operator=principal.user_id,
    )
