"""MCP 运维
周期 probe_all → 更新 _health/_evicted；被驱逐的服务器工具不再 register 到 tool_registry。

MCP ops: probe health, evict failing servers, restore when healthy.
Periodic probe_all updates health; evicted servers' tools are withheld from registry."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from typing import Any, Optional

import httpx

from app.config.settings import settings
from app.services.mcp_bridge import list_mcp_servers_health

logger = logging.getLogger(__name__)


class MCPServerManager:
    """Track MCP server health, evict failing servers, attempt restore."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._health: dict[str, bool] = {}
        self._failures: dict[str, int] = {}
        self._evicted: set[str] = set()
        self._last_probe: dict[str, float] = {}

    def _server_configs(self) -> dict[str, dict[str, Any]]:
        return {
            str(s.get("name", f"mcp_{i}")): s
            for i, s in enumerate(settings.MCP_SERVERS)
            if isinstance(s, dict)
        }

    def probe_all(self) -> dict[str, bool]:
        results = list_mcp_servers_health()
        now = time.monotonic()
        with self._lock:
            for item in results:
                name = str(item.get("name", ""))
                healthy = bool(item.get("healthy"))
                self._health[name] = healthy
                self._last_probe[name] = now
                if healthy:
                    self._failures[name] = 0
                    if name in self._evicted:
                        self._evicted.discard(name)
                        _inc_mcp_metric("restore", name)
                else:
                    self._failures[name] = self._failures.get(name, 0) + 1
                _set_mcp_health_gauge(name, healthy)
        return dict(self._health)

    def evict_unhealthy(self, *, max_failures: int | None = None) -> list[str]:
        threshold = max_failures or settings.MCP_MAX_FAILURES_BEFORE_EVICT
        evicted: list[str] = []
        with self._lock:
            for name, count in list(self._failures.items()):
                if count >= threshold and name not in self._evicted:
                    self._evicted.add(name)
                    evicted.append(name)
                    _inc_mcp_metric("eviction", name)
        return evicted

    def is_active(self, name: str) -> bool:
        with self._lock:
            return name not in self._evicted

    def active_servers(self) -> list[dict[str, Any]]:
        configs = self._server_configs()
        with self._lock:
            return [
                configs[name]
                for name in configs
                if name not in self._evicted
            ]

    def restore_server(self, name: str) -> bool:
        configs = self._server_configs()
        server = configs.get(name)
        if not server:
            return False
        healthy = _probe_single_server(server)
        with self._lock:
            self._health[name] = healthy
            if healthy:
                self._evicted.discard(name)
                self._failures[name] = 0
                _inc_mcp_metric("restore", name)
                _set_mcp_health_gauge(name, True)
                return True
            self._failures[name] = self._failures.get(name, 0) + 1
            _set_mcp_health_gauge(name, False)
        return False

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "health": dict(self._health),
                "failures": dict(self._failures),
                "evicted": sorted(self._evicted),
            }


_manager: MCPServerManager | None = None


def get_mcp_manager() -> MCPServerManager:
    global _manager
    if _manager is None:
        _manager = MCPServerManager()
    return _manager


def reset_mcp_manager() -> None:
    global _manager
    _manager = None


def _probe_single_server(server: dict[str, Any]) -> bool:
    name = str(server.get("name", ""))
    for item in list_mcp_servers_health():
        if str(item.get("name")) == name:
            return bool(item.get("healthy"))
    return False


def list_mcp_resources(server: dict[str, Any]) -> list[dict[str, Any]]:
    """Call MCP resources/list when supported."""
    transport = str(server.get("transport", "http")).lower()
    timeout = int(server.get("timeout", settings.MCP_TIMEOUT))
    if transport == "stdio":
        return _stdio_jsonrpc(server, "resources/list", timeout).get("resources", [])
    base_url = str(server.get("url", "")).strip()
    if not base_url:
        return []
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(base_url.rstrip("/") + "/resources")
            if response.status_code == 404:
                return []
            response.raise_for_status()
            data = response.json()
        resources = data.get("resources", data)
        return resources if isinstance(resources, list) else []
    except Exception as exc:
        logger.debug("MCP resources/list failed for %s: %s", base_url, exc)
        return []


def list_mcp_prompts(server: dict[str, Any]) -> list[dict[str, Any]]:
    transport = str(server.get("transport", "http")).lower()
    timeout = int(server.get("timeout", settings.MCP_TIMEOUT))
    if transport == "stdio":
        return _stdio_jsonrpc(server, "prompts/list", timeout).get("prompts", [])
    base_url = str(server.get("url", "")).strip()
    if not base_url:
        return []
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(base_url.rstrip("/") + "/prompts")
            if response.status_code == 404:
                return []
            response.raise_for_status()
            data = response.json()
        prompts = data.get("prompts", data)
        return prompts if isinstance(prompts, list) else []
    except Exception as exc:
        logger.debug("MCP prompts/list failed for %s: %s", base_url, exc)
        return []


def read_mcp_resource(server: dict[str, Any], uri: str) -> dict[str, Any]:
    transport = str(server.get("transport", "http")).lower()
    timeout = int(server.get("timeout", settings.MCP_TIMEOUT))
    if transport == "stdio":
        result = _stdio_jsonrpc(
            server,
            "resources/read",
            timeout,
            params={"uri": uri},
        )
        return result if isinstance(result, dict) else {"contents": result}
    base_url = str(server.get("url", "")).strip()
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            base_url.rstrip("/") + "/resources/read",
            json={"uri": uri},
        )
        response.raise_for_status()
        return response.json()


def _stdio_jsonrpc(
    server: dict[str, Any],
    method: str,
    timeout: int,
    *,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    command = str(server.get("command", "")).strip()
    if not command:
        return {}
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        request["params"] = params
    proc = subprocess.run(
        [command, *[str(a) for a in server.get("args", [])]],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or f"MCP exited {proc.returncode}")
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout else "{}"
    payload = json.loads(line)
    if "error" in payload:
        raise RuntimeError(str(payload["error"]))
    result = payload.get("result", {})
    return result if isinstance(result, dict) else {}


def _set_mcp_health_gauge(name: str, healthy: bool) -> None:
    if not settings.METRICS_ENABLED:
        return
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().set_mcp_server_health(name, healthy)
    except Exception:
        pass


def _inc_mcp_metric(reason: str, name: str) -> None:
    if not settings.METRICS_ENABLED:
        return
    try:
        from app.services.metrics_service import get_metrics_service

        get_metrics_service().inc_mcp_eviction(name, reason)
    except Exception:
        pass
