"""MCP 桥接：将外部 MCP 服务注册为 tool_registry 上的 ToolSpec。

register_mcp_tools 在 tool_bootstrap 调用；stdio 或 HTTP 传输；不健康 server 被驱逐。

MCP bridge registers external servers as prefixed tools on tool_registry.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from typing import Any

import httpx

from app.config.settings import settings
from app.services.tool_registry import ToolRegistry, ToolSpec

logger = logging.getLogger(__name__)


def _stdio_handler(command: str, args: list[str], tool_name: str, timeout: int) -> Any:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params},
        }
        proc = subprocess.run(
            [command, *args],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or f"MCP process exited {proc.returncode}")
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout else "{}"
        payload = json.loads(line)
        if "error" in payload:
            raise RuntimeError(str(payload["error"]))
        return payload.get("result", {})

    return handler


def _http_list_tools(base_url: str, timeout: int) -> list[dict[str, Any]]:
    url = base_url.rstrip("/") + "/tools"
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        data = response.json()
    tools = data.get("tools", data)
    return tools if isinstance(tools, list) else []


def _http_call_tool(base_url: str, tool_name: str, timeout: int) -> Any:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        url = base_url.rstrip("/") + f"/tools/{tool_name}/call"
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json={"arguments": params})
            response.raise_for_status()
            return response.json()

    return handler


def _register_stdio_server(registry: ToolRegistry, server: dict[str, Any]) -> int:
    command = str(server.get("command", "")).strip()
    if not command:
        return 0
    args = [str(a) for a in server.get("args", [])]
    timeout = int(server.get("timeout", settings.MCP_TIMEOUT))
    prefix = str(server.get("prefix", server.get("name", "mcp")))

    list_request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    proc = subprocess.run(
        [command, *args],
        input=list_request,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("MCP stdio server failed: %s", proc.stderr)
        return 0
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout else "{}"
    payload = json.loads(line)
    tools = payload.get("result", {}).get("tools", [])
    count = 0
    for tool in tools:
        tool_name = str(tool.get("name", ""))
        if not tool_name:
            continue
        registry_name = f"{prefix}_{tool_name}"
        registry.register(
            ToolSpec(
                name=registry_name,
                description=str(tool.get("description", f"MCP tool {tool_name}")),
                input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
                output_schema={"type": "object"},
                required_role=str(server.get("required_role", "user")),
                risk_level=str(server.get("risk_level", "MEDIUM")),
                handler=_stdio_handler(command, args, tool_name, timeout),
            )
        )
        count += 1
    return count


def _active_mcp_servers() -> list[dict[str, Any]]:
    if not settings.MCP_ENABLED:
        return []
    from app.services.mcp_manager import get_mcp_manager

    manager = get_mcp_manager()
    if settings.MCP_AUTO_EVICT:
        manager.probe_all()
        manager.evict_unhealthy()
    return manager.active_servers()


def register_mcp_tools(registry: ToolRegistry) -> int:
    """遍历 MCP_SERVERS 注册工具，返回注册数量。

    Register all active MCP tools; return count.
    """
    if not settings.MCP_ENABLED:
        return 0
    total = 0
    for server in _active_mcp_servers():
        transport = str(server.get("transport", "http")).lower()
        timeout = int(server.get("timeout", settings.MCP_TIMEOUT))
        prefix = str(server.get("prefix", server.get("name", "mcp")))
        if transport == "stdio":
            total += _register_stdio_server(registry, server)
            if settings.MCP_REGISTER_RESOURCES:
                total += _register_mcp_resources(registry, server, prefix, timeout)
            if settings.MCP_REGISTER_PROMPTS:
                total += _register_mcp_prompts(registry, server, prefix, timeout)
            continue
        base_url = str(server.get("url", "")).strip()
        if not base_url:
            continue
        try:
            tools = _http_list_tools(base_url, timeout)
        except Exception as exc:
            logger.warning("MCP HTTP server unavailable %s: %s", base_url, exc)
            continue
        for tool in tools:
            tool_name = str(tool.get("name", ""))
            if not tool_name:
                continue
            registry_name = f"{prefix}_{tool_name}"
            registry.register(
                ToolSpec(
                    name=registry_name,
                    description=str(tool.get("description", f"MCP tool {tool_name}")),
                    input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
                    output_schema={"type": "object"},
                    required_role=str(server.get("required_role", "user")),
                    risk_level=str(server.get("risk_level", "MEDIUM")),
                    handler=_http_call_tool(base_url, tool_name, timeout),
                )
            )
            total += 1
        if settings.MCP_REGISTER_RESOURCES:
            total += _register_mcp_resources(registry, server, prefix, timeout)
        if settings.MCP_REGISTER_PROMPTS:
            total += _register_mcp_prompts(registry, server, prefix, timeout)
    return total


def _register_mcp_resources(
    registry: ToolRegistry,
    server: dict[str, Any],
    prefix: str,
    timeout: int,
) -> int:
    from app.services.mcp_manager import list_mcp_resources, read_mcp_resource

    name = str(server.get("name", prefix))
    count = 0
    try:
        resources = list_mcp_resources(server)
    except Exception as exc:
        logger.warning("MCP resources for %s: %s", name, exc)
        return 0
    for res in resources:
        uri = str(res.get("uri", ""))
        if not uri:
            continue
        tool_name = f"{prefix}_resource_{uri.replace('://', '_').replace('/', '_')[:48]}"

        def _handler(params: dict[str, Any], *, _uri=uri, _server=server) -> dict[str, Any]:
            return read_mcp_resource(_server, _uri)

        registry.register(
            ToolSpec(
                name=tool_name,
                description=str(res.get("description", f"MCP resource {uri}"))[:200],
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                required_role=str(server.get("required_role", "user")),
                risk_level="LOW",
                handler=_handler,
            )
        )
        count += 1
    return count


def _register_mcp_prompts(
    registry: ToolRegistry,
    server: dict[str, Any],
    prefix: str,
    timeout: int,
) -> int:
    from app.services.mcp_manager import list_mcp_prompts

    count = 0
    try:
        prompts = list_mcp_prompts(server)
    except Exception as exc:
        logger.warning("MCP prompts for %s: %s", server.get("name"), exc)
        return 0
    for prompt in prompts:
        prompt_name = str(prompt.get("name", ""))
        if not prompt_name:
            continue
        registry_name = f"{prefix}_prompt_{prompt_name}"

        def _handler(params: dict[str, Any], *, _p=prompt) -> dict[str, Any]:
            return {"prompt": _p, "params": params}

        registry.register(
            ToolSpec(
                name=registry_name,
                description=str(prompt.get("description", f"MCP prompt {prompt_name}"))[:200],
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object"},
                required_role=str(server.get("required_role", "user")),
                risk_level="LOW",
                handler=_handler,
            )
        )
        count += 1
    return count


def list_mcp_servers_health() -> list[dict[str, Any]]:
    """Probe configured MCP servers for health endpoint / list tools."""
    if not settings.MCP_ENABLED:
        return []
    results: list[dict[str, Any]] = []
    for server in settings.MCP_SERVERS:
        name = str(server.get("name", "mcp"))
        transport = str(server.get("transport", "http")).lower()
        healthy = False
        detail = ""
        if transport == "stdio":
            command = str(server.get("command", "")).strip()
            if command:
                try:
                    list_request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
                    proc = subprocess.run(
                        [command, *[str(a) for a in server.get("args", [])]],
                        input=list_request,
                        capture_output=True,
                        text=True,
                        timeout=min(10, int(server.get("timeout", settings.MCP_TIMEOUT))),
                        check=False,
                    )
                    healthy = proc.returncode == 0
                    detail = "" if healthy else (proc.stderr or "stdio failed")[:200]
                except Exception as exc:
                    detail = str(exc)[:200]
        else:
            base_url = str(server.get("url", "")).strip()
            if base_url:
                try:
                    _http_list_tools(base_url, int(server.get("timeout", settings.MCP_TIMEOUT)))
                    healthy = True
                except Exception as exc:
                    detail = str(exc)[:200]
        results.append(
            {"name": name, "transport": transport, "healthy": healthy, "detail": detail}
        )
    return results
