"""Skill marketplace stub API (Phase 4 — catalog preview only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_current_principal
from app.api.skills_api import _require_skill_platform
from app.services.auth_service import AuthPrincipal
from app.services.skill_hooks import list_trusted_hooks_detail
from app.services.skill_package_loader import list_installed_packages

router = APIRouter(prefix="/skills/marketplace", tags=["skills-marketplace"])


@router.get("/listings")
def marketplace_listings(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Preview marketplace listings (not transactional)."""
    _require_skill_platform()
    return {
        "status": "preview",
        "message": "Marketplace transactions are not enabled in this release.",
        "listings": [],
    }


@router.get("/packages")
def marketplace_packages(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Installed skill packages on this runtime."""
    _require_skill_platform()
    packages = list_installed_packages()
    return {
        "status": "installed",
        "packages": packages,
        "total": len(packages),
    }


@router.get("/hooks")
def marketplace_trusted_hooks(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, Any]:
    """Trusted plugin_ref hooks registered on this node."""
    _require_skill_platform()
    hooks = list_trusted_hooks_detail()
    return {"hooks": hooks, "total": len(hooks)}
