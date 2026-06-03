"""Skill management API (Phase 2) — CRUD, publish, versions."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.api.skills_api import _definition_to_summary, _require_skill_platform
from app.domain.skill_models import (
    SkillDefinition,
    SkillOwnerType,
    SkillPresentation,
    SkillSourceType,
    SkillStatus,
    SkillVisibility,
)
from app.services.auth_service import AuthPrincipal
from app.services.skill_permissions import can_manage_definition, require_skill_permission
from app.services.skill_registry import get_skill_registry
from app.services.skill_store import get_skill_store, normalize_skill_id
from app.services.tenant_context import get_tenant_id

router = APIRouter(prefix="/skills", tags=["skills-manage"])


class SkillWriteRequest(BaseModel):
    skill_id: Optional[str] = None
    name: str
    description: str = ""
    summary: str = ""
    base_domain: str = "single_turn"
    category: str = "general"
    preferred_execution_mode: str = "single"
    allowed_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    planning_overlay: str = ""
    reasoning_overlay: str = ""
    reflection_overlay: str = ""
    output_contract: dict[str, Any] = Field(default_factory=dict)
    action_policy: dict[str, Any] = Field(default_factory=dict)
    examples: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    risk_level: str = "LOW"
    required_role: str = "user"
    visibility: str = "tenant_private"
    presentation: Optional[dict[str, Any]] = None


class SkillCloneRequest(BaseModel):
    new_skill_id: Optional[str] = None
    name: Optional[str] = None


class SkillPublishRequest(BaseModel):
    change_log: str = ""


def _tenant_owner_id(principal: AuthPrincipal) -> str:
    return get_tenant_id() or principal.tenant_id or principal.user_id or "default"


def _request_to_definition(body: SkillWriteRequest, *, skill_id: str, owner_id: str) -> SkillDefinition:
    pres_raw = body.presentation or {}
    presentation = SkillPresentation.model_validate(pres_raw) if pres_raw else SkillPresentation()
    try:
        visibility = SkillVisibility(body.visibility.lower())
    except ValueError:
        visibility = SkillVisibility.TENANT_PRIVATE
    return SkillDefinition(
        skill_id=skill_id,
        name=body.name,
        description=body.description,
        summary=body.summary or body.description,
        source_type=SkillSourceType.TENANT,
        owner_type=SkillOwnerType.TENANT,
        owner_id=owner_id,
        base_domain=body.base_domain,
        category=body.category,
        preferred_execution_mode=body.preferred_execution_mode,
        allowed_tools=body.allowed_tools,
        blocked_tools=body.blocked_tools,
        planning_overlay=body.planning_overlay,
        reasoning_overlay=body.reasoning_overlay,
        reflection_overlay=body.reflection_overlay,
        output_contract=body.output_contract,
        action_policy=body.action_policy,
        examples=body.examples,
        tags=body.tags,
        risk_level=body.risk_level,
        required_role=body.required_role,
        visibility=visibility,
        status=SkillStatus.DRAFT,
        presentation=presentation,
    )


def _ensure_manageable(principal: AuthPrincipal, defn: SkillDefinition) -> None:
    if get_skill_registry().is_builtin(defn.skill_id):
        raise HTTPException(status_code=403, detail="Built-in skills cannot be modified")
    if not can_manage_definition(principal, defn.owner_id, defn.owner_type.value):
        raise HTTPException(status_code=403, detail="Not allowed to manage this skill")


@router.post("", status_code=201)
def create_skill(
    body: SkillWriteRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.create")
    registry = get_skill_registry()
    raw_id = body.skill_id or body.name
    try:
        skill_id = normalize_skill_id(str(raw_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    owner_id = _tenant_owner_id(principal)
    if registry.is_builtin(skill_id):
        raise HTTPException(status_code=409, detail=f"Skill id conflicts with built-in: {skill_id}")
    try:
        registry.load_definition(skill_id, tenant_id=owner_id)
        raise HTTPException(status_code=409, detail=f"Skill already exists: {skill_id}")
    except KeyError:
        pass
    defn = _request_to_definition(body, skill_id=skill_id, owner_id=owner_id)
    store = get_skill_store()
    saved = store.save_draft(defn, tenant_id=owner_id, operator=principal.user_id)
    return {"skill": _definition_to_summary(saved), "status": saved.status.value}


@router.put("/{skill_id}")
def update_skill(
    skill_id: str,
    body: SkillWriteRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.update")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    existing = store.load(skill_id, owner_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    _ensure_manageable(principal, existing)
    if existing.status != SkillStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only draft skills can be edited")
    updated = _request_to_definition(body, skill_id=skill_id, owner_id=owner_id)
    updated.version = existing.version
    saved = store.save_draft(updated, tenant_id=owner_id, operator=principal.user_id)
    return {"skill": _definition_to_summary(saved), "status": saved.status.value}


@router.post("/{skill_id}/publish")
def publish_skill(
    skill_id: str,
    body: SkillPublishRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.publish")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    existing = store.load(skill_id, owner_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    _ensure_manageable(principal, existing)
    try:
        published = store.publish(
            skill_id,
            tenant_id=owner_id,
            operator=principal.user_id,
            change_log=body.change_log,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"skill": _definition_to_summary(published), "version": published.version}


@router.post("/{skill_id}/archive")
def archive_skill(
    skill_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.update")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    existing = store.load(skill_id, owner_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    _ensure_manageable(principal, existing)
    archived = store.archive(skill_id, tenant_id=owner_id, operator=principal.user_id)
    return {"skill": _definition_to_summary(archived), "status": archived.status.value}


@router.post("/{skill_id}/disable")
def disable_skill(
    skill_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.disable")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    existing = store.load(skill_id, owner_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    _ensure_manageable(principal, existing)
    disabled = store.disable(skill_id, tenant_id=owner_id, operator=principal.user_id)
    return {"skill": _definition_to_summary(disabled), "status": disabled.status.value}


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.delete")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    existing = store.load(skill_id, owner_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    _ensure_manageable(principal, existing)
    deleted = store.delete(skill_id, tenant_id=owner_id)
    return {"skill_id": skill_id, "deleted": deleted}


@router.post("/{skill_id}/clone")
def clone_skill(
    skill_id: str,
    body: SkillCloneRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.clone")
    registry = get_skill_registry()
    owner_id = _tenant_owner_id(principal)
    try:
        source = registry.load_definition(skill_id, tenant_id=owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    new_id_raw = body.new_skill_id or f"{skill_id}_copy"
    try:
        new_id = normalize_skill_id(new_id_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if registry.is_builtin(new_id):
        raise HTTPException(status_code=409, detail="skill_id conflicts with built-in")
    store = get_skill_store()
    try:
        clone = store.clone_from(
            source,
            new_skill_id=new_id,
            tenant_id=owner_id,
            operator=principal.user_id,
            owner_id=owner_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.name:
        clone.name = body.name
        store.save_draft(clone, tenant_id=owner_id, operator=principal.user_id)
    return {"skill": _definition_to_summary(clone), "cloned_from": skill_id}


@router.get("/{skill_id}/versions")
def list_skill_versions(
    skill_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.read")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    if not store.load(skill_id, owner_id) and not get_skill_registry().is_builtin(skill_id):
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    versions = store.list_versions(skill_id, owner_id)
    return {
        "skill_id": skill_id,
        "versions": [v.model_dump(mode="json") for v in versions],
        "total": len(versions),
    }


@router.post("/{skill_id}/versions/{version}/rollback")
def rollback_skill(
    skill_id: str,
    version: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.rollback")
    owner_id = _tenant_owner_id(principal)
    store = get_skill_store()
    existing = store.load(skill_id, owner_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    _ensure_manageable(principal, existing)
    try:
        restored = store.rollback(
            skill_id,
            version,
            tenant_id=owner_id,
            operator=principal.user_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"skill": _definition_to_summary(restored), "rolled_back_to": version}
