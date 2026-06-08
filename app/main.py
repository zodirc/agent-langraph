"""FastAPI 入口：挂载 API 路由与 Web 静态页。

启动流程 lifespan：logging → 安全校验 → LangSmith → 存储初始化 → 知识库种子
→ 工具与 MCP 注册 → 定时调度与审核超时 → 可选 A2A 自注册。
运行时：/tasks → graph_runner；/tasks/{id}/message/stream → SSE；/skills → 技能目录。

FastAPI entry: API routers and static web.
lifespan initializes logging, storage, knowledge, tools, scheduler, optional A2A.
Runtime routes tasks to graph_runner and skills catalog API.
"""

from __future__ import annotations

import logging
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api.health_api import router as health_router
from app.api.artifacts_api import router as artifacts_router
from app.api.auth_api import router as auth_router
from app.api.batch_api import router as batch_router
from app.api.dead_letter_api import router as dead_letter_router
from app.api.a2a_api import router as a2a_router
from app.api.domains_api import router as domains_router
from app.api.feedback_api import router as feedback_router
from app.api.supervisor_api import router as supervisor_router
from app.api.knowledge_api import router as knowledge_router
from app.api.metrics_api import router as metrics_router
from app.api.review_api import router as review_router
from app.api.schedule_api import router as schedule_router
from app.api.tenant_api import router as tenant_router
from app.api.task_api import router as task_router
from app.api.skills_api import router as skills_router
from app.api.skills_manage_api import router as skills_manage_router
from app.api.skills_governance_api import router as skills_governance_router
from app.api.skills_marketplace_api import router as skills_marketplace_router
from app.api.tools_api import router as tools_router
import app.config.settings as settings_module
from app.services.knowledge_seed import ensure_builtin_knowledge, seed_default_knowledge
from app.services.knowledge_store import get_knowledge_store
from app.services.langsmith_setup import configure_langsmith
from app.services.logging_config import setup_logging
from app.services.review_timeout_service import get_review_timeout_service
from app.services.scheduler_service import get_scheduler_service
from app.services.storage_init import init_storage, shutdown_storage
from app.services.tool_bootstrap import bootstrap_tools
from app.services.sqlite_compat import SQLITE_SHIM, sqlite3_version
from app.services.startup_checks import validate_runtime_security
from app.services.tool_registry import get_tool_registry

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
logger = logging.getLogger(__name__)


class DevStaticFiles(StaticFiles):
    """Disable browser cache for /static in development (volume-mounted web/)."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response: Response = await super().get_response(path, scope)
        if settings_module.settings.APP_ENV == "development":
            response.headers["Cache-Control"] = "no-store"
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # One-time process init; shutdown after yield / 进程级一次性初始化
    setup_logging()
    validate_runtime_security()
    configure_langsmith()
    init_storage()  # state/audit/checkpoint 等后端
    seeded = seed_default_knowledge()
    ensure_builtin_knowledge()
    tool_counts = bootstrap_tools()  # 内置 + HTTP + MCP → tool_registry
    get_scheduler_service().start()
    get_review_timeout_service().start()
    if settings_module.settings.A2A_SELF_URL:
        from app.services.agent_registry import get_agent_registry
        from app.services.a2a_dispatch import runtime_agent_card

        get_agent_registry().register(runtime_agent_card(), settings_module.settings.A2A_SELF_URL)
    logger.info(
        "Agent runtime started",
        extra={
            "env": settings_module.settings.APP_ENV,
            "auth_enabled": settings_module.settings.AUTH_ENABLED,
            "knowledge_docs": get_knowledge_store().count(),
            "seeded": seeded,
            "http_tools": tool_counts.get("http", 0),
            "mcp_tools": tool_counts.get("mcp", 0),
            "knowledge_backend": settings_module.settings.KNOWLEDGE_BACKEND,
            "queue_backend": settings_module.settings.QUEUE_BACKEND,
            "storage_backend": settings_module.settings.STORAGE_BACKEND,
            "checkpoint_backend": settings_module.settings.CHECKPOINT_BACKEND,
        },
    )
    yield
    get_review_timeout_service().shutdown()
    get_scheduler_service().shutdown()
    shutdown_storage()


app = FastAPI(
    title="Agent LangGraph Runtime",
    description="Multi-domain agent runtime based on LangGraph",
    version="0.10.0",
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(task_router)
app.include_router(artifacts_router)
app.include_router(review_router)
app.include_router(knowledge_router)
app.include_router(batch_router)
app.include_router(schedule_router)
app.include_router(tools_router)
app.include_router(skills_marketplace_router)
app.include_router(skills_governance_router)
app.include_router(skills_router)
app.include_router(skills_manage_router)
app.include_router(domains_router)
app.include_router(supervisor_router)
app.include_router(metrics_router)
app.include_router(tenant_router)
app.include_router(dead_letter_router)
app.include_router(feedback_router)
app.include_router(a2a_router)

if (WEB_DIR / "static").exists():
    static_cls = DevStaticFiles if settings_module.settings.APP_ENV == "development" else StaticFiles
    app.mount("/static", static_cls(directory=WEB_DIR / "static"), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    """Browser default favicon request."""
    icon = WEB_DIR / "static" / "favicon.svg"
    if not icon.exists():
        raise RuntimeError(f"Favicon not found: {icon}")
    return FileResponse(icon, media_type="image/svg+xml")


@app.get("/")
def platform_home() -> FileResponse:
    """Platform landing page."""
    page = WEB_DIR / "home.html"
    if not page.exists():
        raise RuntimeError(f"Platform home not found: {page}")
    return FileResponse(page)


@app.get("/chat")
def web_chat() -> FileResponse:
    """Web CLI conversation page."""
    page = WEB_DIR / "chat.html"
    if not page.exists():
        raise RuntimeError(f"Chat page not found: {page}")
    return FileResponse(page)


@app.get("/cli")
def web_cli_redirect():
    """Backward-compatible alias for the conversation page."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/chat", status_code=302)


@app.get("/dashboard")
def monitoring_dashboard() -> FileResponse:
    """Task monitoring panel (architecture §16 phase 4)."""
    page = WEB_DIR / "dashboard.html"
    if not page.exists():
        raise RuntimeError(f"Dashboard not found: {page}")
    return FileResponse(page)


