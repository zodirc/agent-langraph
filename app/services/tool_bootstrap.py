"""工具启动注册
顺序 Order: builtin (in ToolRegistry.__init__) → HTTP tools → MCP tools → skill_* tools.

Tool bootstrap at app lifespan (once per process)."""

from __future__ import annotations

import logging

from app.config.settings import settings
from app.services.http_tool_loader import register_http_tools
from app.services.mcp_bridge import register_mcp_tools
from app.services.tool_registry import get_tool_registry

logger = logging.getLogger(__name__)
_bootstrapped = False


def bootstrap_tools() -> dict[str, int]:
    global _bootstrapped
    registry = get_tool_registry()
    if _bootstrapped:
        return {"http": 0, "mcp": 0, "skills": 0, "skipped": 1}
    http_count = register_http_tools(registry)
    mcp_count = register_mcp_tools(registry)
    skill_count = 0
    if settings.SKILL_ENABLED:
        from app.services.skill_registry import get_skill_registry

        skill_count = _register_skills_as_tools(registry, get_skill_registry())
    _bootstrapped = True
    logger.info(
        "External tools registered",
        extra={"http_tools": http_count, "mcp_tools": mcp_count, "total": len(registry.list_tools())},
    )
    return {"http": http_count, "mcp": mcp_count, "skills": skill_count}


def _register_skills_as_tools(registry, skill_registry) -> int:
    from app.services.tool_registry import ToolSpec

    count = 0
    for skill in skill_registry.list_skills(role="guest"):
        tool_name = f"skill_{skill.skill_id}"

        def _handler(params: dict, *, _skill=skill) -> dict:
            from app.services.skill_registry import get_skill_registry

            return get_skill_registry().invoke(
                _skill.skill_id,
                params,
                user_role=str(params.get("user_role", "user")),
            )

        registry.register(
            ToolSpec(
                name=tool_name,
                description=skill.description or skill.name,
                input_schema=skill.input_schema or {"type": "object", "properties": {}},
                output_schema=skill.output_schema or {"type": "object", "properties": {}},
                required_role=skill.required_role,
                risk_level=skill.risk_level,
                handler=_handler,
            )
        )
        count += 1
    return count


def reset_tool_bootstrap() -> None:
    global _bootstrapped
    _bootstrapped = False
