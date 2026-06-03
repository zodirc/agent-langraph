"""Skill 目录 API

Skill catalog API: list, detail, dry-run for Web /skills.
Execution policy is NOT resolved here; task_api uses skill_resolver.attach at create time."""

from __future__ import annotations

from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app.api.deps import _authenticate_principal, get_current_principal
from app.services.tenant_context import apply_tenant_headers
from app.config.settings import settings
from app.domain.skill_models import SkillStatus
from app.services.auth_service import AuthPrincipal
from app.services.skill_permissions import can_manage_definition, require_skill_permission
from app.services.skill_registry import get_skill_registry
from app.services.skill_dry_run import dry_run_skill
from app.services.skill_resolver import (
    SkillNotFoundError,
    SkillPermissionError,
    SkillResolveError,
    resolve_skill_for_task,
)
from app.services.skill_store import get_skill_store
from app.services.tenant_context import get_tenant_id

router = APIRouter(prefix="/skills", tags=["skills"])

_WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def _wants_html_page(request: Request) -> bool:
    """Browser navigation (text/html) vs API fetch (JSON / */*)."""
    accept = (request.headers.get("accept") or "").lower()
    return "text/html" in accept and "application/json" not in accept


_skills_bearer = HTTPBearer(auto_error=False)


def _principal_for_skills_list(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_skills_bearer),
) -> AuthPrincipal:
    """Allow unauthenticated HTML catalog; API callers still require auth when enabled."""
    if _wants_html_page(request):
        apply_tenant_headers(dict(request.headers))
        return AuthPrincipal(user_id="web", role="user", auth_method="html")
    apply_tenant_headers(dict(request.headers))
    return _authenticate_principal(request, credentials)


def _require_skill_platform() -> None:
    if not settings.SKILL_ENABLED and not settings.SKILL_RUNTIME_POLICY_ENABLED:
        raise HTTPException(status_code=503, detail="Skill platform is disabled")


def _definition_to_summary(defn: Any) -> dict[str, Any]:
    pres = defn.presentation
    return {
        "skill_id": defn.skill_id,
        "slug": defn.slug,
        "name": defn.name,
        "version": defn.version,
        "summary": defn.summary or defn.description,
        "description": defn.description,
        "category": defn.category,
        "base_domain": defn.base_domain,
        "tags": defn.tags,
        "status": defn.status.value,
        "visibility": defn.visibility.value,
        "source_type": defn.source_type.value,
        "risk_level": defn.risk_level,
        "preferred_execution_mode": defn.preferred_execution_mode,
        "icon": pres.icon,
        "badges": pres.badges,
        "display_order": pres.display_order,
    }


@router.get("")
def list_skills(
    request: Request,
    scope: str = Query("all", description="system|mine|tenant|all"),
    status: str = Query("published"),
    domain: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    principal: AuthPrincipal = Depends(_principal_for_skills_list),
) -> dict[str, Any]:
    if _wants_html_page(request):
        page = _WEB_DIR / "skills.html"
        if page.is_file():
            return FileResponse(page)
    _require_skill_platform()
    registry = get_skill_registry()
    status_enum = SkillStatus.PUBLISHED
    if status:
        try:
            status_enum = SkillStatus(status.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from None

    tenant_id = get_tenant_id() or principal.tenant_id
    defs = registry.list_definitions(
        role=principal.role,
        status=status_enum,
        domain=domain,
        query=q,
        tenant_id=tenant_id,
    )
    if category:
        defs = [d for d in defs if d.category.lower() == category.lower()]
    if scope == "system":
        defs = [
            d
            for d in defs
            if d.source_type.value in ("builtin", "system_package")
        ]
    elif scope == "package":
        defs = [d for d in defs if d.source_type.value == "system_package"]
    elif scope in ("mine", "tenant"):
        owner_id = tenant_id or principal.user_id or "default"
        defs = [
            d
            for d in defs
            if d.source_type.value == "tenant" and d.owner_id == owner_id
        ]

    return {
        "skills": [_definition_to_summary(d) for d in defs],
        "total": len(defs),
    }


@router.get("/hooks/trusted")
def list_trusted_skill_hooks(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    from app.services.skill_hooks import list_trusted_hooks_detail

    hooks = list_trusted_hooks_detail()
    return {"hooks": hooks, "total": len(hooks)}


@router.get("/catalog/tenant")
def tenant_skill_catalog(
    principal: AuthPrincipal = Depends(get_current_principal),
    status: str = Query("published"),
) -> dict[str, Any]:
    """Private tenant catalog: custom skills owned by the current tenant."""
    _require_skill_platform()
    require_skill_permission(principal, "skill.read")
    tenant_id = get_tenant_id() or principal.tenant_id or "default"
    status_enum = SkillStatus.PUBLISHED
    if status:
        try:
            status_enum = SkillStatus(status.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}") from None
    store = get_skill_store()
    custom = store.list_custom(tenant_id, status=status_enum)
    return {
        "tenant_id": tenant_id,
        "skills": [_definition_to_summary(s) for s in custom],
        "total": len(custom),
    }


@router.get("/catalog/categories")
def list_categories(
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    tenant_id = get_tenant_id() or principal.tenant_id
    return {
        "groups": get_skill_registry().categories_summary(
            role=principal.role,
            tenant_id=tenant_id,
        )
    }


@router.get("/test", include_in_schema=False)
def skills_test_page() -> FileResponse:
    page = _WEB_DIR / "skills_test.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Skills test page not found")
    return FileResponse(page)


@router.get("/manage", include_in_schema=False)
def skills_manage_page() -> FileResponse:
    page = _WEB_DIR / "skills_manage.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="Skills manage page not found")
    return FileResponse(page)


@router.get("/{skill_id}")
def get_skill(
    skill_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    registry = get_skill_registry()
    tenant_id = get_tenant_id() or principal.tenant_id
    try:
        defn = registry.load_definition(skill_id, tenant_id=tenant_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}") from exc
    if not registry.role_allows(defn.required_role, principal.role):
        raise HTTPException(status_code=403, detail="Permission denied")
    pres = defn.presentation
    custom = get_skill_store().load(skill_id, tenant_id)
    editable = (
        custom is not None
        and not registry.is_builtin(skill_id)
        and can_manage_definition(principal, defn.owner_id, defn.owner_type.value)
    )
    return {
        "definition": defn.model_dump(mode="json"),
        "presentation": pres.model_dump(mode="json"),
        "tools": {
            "allowed": defn.allowed_tools,
            "blocked": defn.blocked_tools,
        },
        "examples": defn.examples,
        "editable": editable,
        "version_summary": {"current": defn.version, "status": defn.status.value},
    }


class SkillDryRunRequest(BaseModel):
    goal: str = ""
    skill_params: dict[str, Any] = Field(default_factory=dict)
    domain: Optional[str] = None


@router.post("/{skill_id}/dry-run")
def dry_run_skill_endpoint(
    skill_id: str,
    body: SkillDryRunRequest,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    require_skill_permission(principal, "skill.read")
    try:
        return dry_run_skill(
            skill_id,
            goal=body.goal,
            user_role=principal.role,
            tenant_id=get_tenant_id(),
            skill_params=body.skill_params,
            domain=body.domain,
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SkillResolveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{skill_id}/validate")
def validate_skill(
    skill_id: str,
    principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    _require_skill_platform()
    registry = get_skill_registry()
    try:
        defn = registry.load_definition(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from app.services.skill_validator import validate_skill_definition
    from app.services.tool_registry import get_tool_registry

    issues = validate_skill_definition(defn, known_tools=get_tool_registry().list_tools())
    try:
        _, policy, _ = resolve_skill_for_task(
            skill_id,
            user_role=principal.role,
        )
        policy_preview = policy.to_dict()
    except Exception as exc:
        issues.append(f"resolve: {exc}")
        policy_preview = None
    return {"skill_id": skill_id, "valid": not issues, "issues": issues, "runtime_policy": policy_preview}
