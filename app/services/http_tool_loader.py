from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config.settings import settings
from app.services.tool_registry import ToolRegistry, ToolSpec

logger = logging.getLogger(__name__)


def _make_http_handler(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    query_params: dict[str, str],
    body_template: dict[str, Any],
    timeout: int,
) -> Any:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        req_headers = dict(headers)
        query = dict(query_params)
        for key, value in params.items():
            if key in query:
                query[key] = str(value)
        target_url = url
        for key, value in params.items():
            target_url = target_url.replace(f"{{{key}}}", str(value))

        body = dict(body_template)
        for key, value in params.items():
            if key in body:
                body[key] = value

        with httpx.Client(timeout=timeout) as client:
            response = client.request(
                method=method.upper(),
                url=target_url,
                headers=req_headers,
                params=query,
                json=body if method.upper() in ("POST", "PUT", "PATCH") else None,
            )
        return {
            "status_code": response.status_code,
            "body": response.text[:4000],
            "ok": response.is_success,
        }

    return handler


def register_http_tools(registry: ToolRegistry) -> int:
    count = 0
    for tool_cfg in settings.HTTP_TOOLS:
        name = str(tool_cfg.get("name", "")).strip()
        if not name:
            continue
        url = str(tool_cfg.get("url", "")).strip()
        if not url:
            logger.warning("HTTP tool %s missing url, skipped", name)
            continue
        properties = tool_cfg.get("input_schema", {}).get("properties")
        if not isinstance(properties, dict):
            properties = {"input": {"type": "string"}}

        registry.register(
            ToolSpec(
                name=name,
                description=str(tool_cfg.get("description", f"HTTP tool {name}")),
                input_schema={"type": "object", "properties": properties},
                output_schema={"type": "object"},
                required_role=str(tool_cfg.get("required_role", "user")),
                risk_level=str(tool_cfg.get("risk_level", "LOW")),
                handler=_make_http_handler(
                    url=url,
                    method=str(tool_cfg.get("method", "GET")),
                    headers=tool_cfg.get("headers", {}) or {},
                    query_params=tool_cfg.get("query_params", {}) or {},
                    body_template=tool_cfg.get("body_template", {}) or {},
                    timeout=int(tool_cfg.get("timeout", settings.HTTP_TOOL_TIMEOUT)),
                ),
            )
        )
        count += 1
    return count
