"""Runtime model configuration API — switch provider/model/key without restart."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_principal
from app.services.auth_service import AuthPrincipal
from app.services.runtime_model_config import get_runtime_model_config

router = APIRouter(prefix="/model", tags=["model"])


class ModelConfigUpdate(BaseModel):
    provider: Optional[str] = Field(None, max_length=64)
    model_name: Optional[str] = Field(None, max_length=256)
    api_key: Optional[str] = Field(None, max_length=512)
    base_url: Optional[str] = Field(None, max_length=512)
    enabled: Optional[bool] = None


@router.get("/config")
def get_model_config(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, object]:
    """Current effective model settings (API key masked)."""
    return get_runtime_model_config().for_api()


@router.put("/config")
def put_model_config(
    body: ModelConfigUpdate,
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, object]:
    """Apply runtime model override; takes effect immediately for new LLM calls."""
    if not any(
        v is not None
        for v in (body.provider, body.model_name, body.api_key, body.base_url, body.enabled)
    ):
        raise HTTPException(status_code=400, detail="At least one field is required")

    eff = get_runtime_model_config().apply(
        provider=body.provider,
        model_name=body.model_name,
        api_key=body.api_key,
        base_url=body.base_url,
        enabled=body.enabled,
    )
    payload = get_runtime_model_config().for_api()
    payload["applied"] = {
        "provider": eff.provider,
        "model_name": eff.model_name,
        "enabled": eff.enabled,
    }
    return payload


@router.post("/config/reset")
def reset_model_config(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, object]:
    """Discard runtime override and revert to startup .env / YAML settings."""
    get_runtime_model_config().reset()
    return get_runtime_model_config().for_api()


@router.post("/config/probe")
def probe_model_config(
    _principal: AuthPrincipal = Depends(get_current_principal),
) -> dict[str, object]:
    """Lightweight connectivity check using the effective model configuration."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.services.llm_client import get_llm
    from app.services.runtime_model_config import get_effective_model_config

    eff = get_effective_model_config()
    if not eff.enabled:
        raise HTTPException(status_code=400, detail="Model is disabled or API key missing")

    llm = get_llm("routing")
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM client not available")

    try:
        response = llm.invoke(
            [
                SystemMessage(content="Reply with exactly: ok"),
                HumanMessage(content="ping"),
            ]
        )
        content = response.content if hasattr(response, "content") else str(response)
        preview = str(content)[:240]
        return {
            "status": "ok",
            "provider": eff.provider,
            "model_name": eff.model_name,
            "response_preview": preview,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Model probe failed: {exc}",
        ) from exc
