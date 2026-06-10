from __future__ import annotations

from fastapi import APIRouter

from app.config.settings import settings
from app.services.health_checks import run_all_checks
from app.services.knowledge_store import get_knowledge_store
from app.services.tool_registry import get_tool_registry

router = APIRouter(tags=["health"])


@router.get("/health/live")
def health_live() -> dict[str, str]:
    """Liveness — process is up."""
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready() -> dict[str, object]:
    """Readiness — dependencies required to serve traffic."""
    result = run_all_checks()
    status = "ok" if result["ready"] else "degraded"
    return {"status": status, **result}


@router.get("/health")
def health_full() -> dict[str, object]:
    """Detailed health with subsystem checks."""
    store = get_knowledge_store()
    vector_ok = bool(getattr(store._vector, "available", False))  # noqa: SLF001
    checks = run_all_checks()
    return {
        "status": "ok" if checks["ready"] else "degraded",
        "env": settings.APP_ENV,
        "version": "0.20.0",
        "model_enabled": settings.MODEL_ENABLED,
        "model_name": settings.MODEL_NAME,
        "model_base_url": settings.MODEL_BASE_URL,
        "model_api_key_configured": bool(str(settings.MODEL_API_KEY or "").strip()),
        "auth_enabled": settings.AUTH_ENABLED,
        "metrics_enabled": settings.METRICS_ENABLED,
        "knowledge_docs": store.count(),
        "knowledge_backend": settings.KNOWLEDGE_BACKEND,
        "vector_index_available": vector_ok,
        "tools_registered": len(get_tool_registry().list_tools()),
        "storage_backend": settings.STORAGE_BACKEND,
        "checkpoint_backend": settings.CHECKPOINT_BACKEND,
        "queue_backend": settings.QUEUE_BACKEND,
        "rate_limit_enabled": settings.RATE_LIMIT_ENABLED,
        "rate_limit_user_per_minute": settings.RATE_LIMIT_USER_PER_MIN,
        "rate_limit_ip_per_minute": settings.RATE_LIMIT_IP_PER_MIN,
        "rate_limit_window_sec": settings.RATE_LIMIT_WINDOW_SEC,
        "secrets_backend": settings.SECRETS_BACKEND,
        "checks": checks["checks"],
    }
