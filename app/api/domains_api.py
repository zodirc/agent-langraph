from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_principal
from app.config.prompt_templates import list_template_catalog
from app.domain.packs.registry import get_domain_pack, list_domain_packs
from app.services.auth_service import AuthPrincipal

router = APIRouter(prefix="/domains", tags=["domains"])


@router.get("")
def list_domains(_principal: AuthPrincipal = Depends(get_current_principal)) -> dict:
    packs = list_domain_packs()
    return {
        "domains": [
            {
                "name": pack.name,
                "description": pack.description,
                "tools": pack.tools,
                "risk_level": pack.risk_level,
            }
            for pack in packs
        ],
        "total": len(packs),
    }


@router.get("/prompt-catalog")
def prompt_catalog(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict:
    """Prompt template library catalog (Appendix A)."""
    return {"templates": list_template_catalog()}


@router.get("/{domain_name}")
def get_domain(
    domain_name: str,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict:
    try:
        pack = get_domain_pack(domain_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "name": pack.name,
        "description": pack.description,
        "tools": pack.tools,
        "planning_hints": pack.planning_hints,
        "risk_level": pack.risk_level,
        "metadata": pack.metadata,
    }
