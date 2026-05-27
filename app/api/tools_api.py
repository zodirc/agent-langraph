from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_principal
from app.services.auth_service import AuthPrincipal
from app.services.tool_registry import get_tool_registry

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
def list_tools(_principal: AuthPrincipal = Depends(get_current_principal)) -> dict:
    registry = get_tool_registry()
    tools = []
    for name in registry.list_tools():
        spec = registry.get(name)
        tools.append(
            {
                "name": spec.name,
                "description": spec.description,
                "required_role": spec.required_role,
                "risk_level": spec.risk_level,
            }
        )
    return {"tools": tools, "total": len(tools)}
