"""E2E: MCP HTTP stub server + manager + tool registration."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.services.mcp_manager import get_mcp_manager, list_mcp_resources, reset_mcp_manager
from app.services.tool_registry import ToolRegistry


@pytest.fixture
def mcp_http_server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_stubs.http_server", "0", "127.0.0.1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = ""
    deadline = time.time() + 5.0
    while time.time() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            time.sleep(0.05)
            continue
        try:
            info = json.loads(line.strip())
            url = str(info.get("url", ""))
            if url:
                break
        except json.JSONDecodeError:
            continue
    if not url:
        proc.kill()
        raise RuntimeError("MCP HTTP stub failed to start")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_mcp_http_register_and_invoke(mcp_http_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "mcp_http.yaml"
    config.write_text(
        f"""
mcp:
  enabled: true
  auto_evict: false
  register_resources: true
  register_prompts: true
  servers:
    - name: http_stub
      transport: http
      url: {mcp_http_server}
      prefix: httpmcp
""",
        encoding="utf-8",
    )
    import app.services.mcp_bridge as mcp_module
    import app.services.mcp_manager as mgr_module

    settings = Settings(str(config))
    monkeypatch.setattr(mcp_module, "settings", settings)
    monkeypatch.setattr(mgr_module, "settings", settings)
    reset_mcp_manager()

    server_cfg = settings.MCP_SERVERS[0]
    resources = list_mcp_resources(server_cfg)
    assert len(resources) >= 1

    registry = ToolRegistry()
    count = mcp_module.register_mcp_tools(registry)
    assert count >= 2
    assert any(n.startswith("httpmcp_") for n in registry.list_tools())

    result = registry.invoke("httpmcp_echo", {"message": "hi"}, user_role="user")
    assert result["result"]["echo"] == "hi"


def test_mcp_http_health_and_eviction(mcp_http_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "mcp_http.yaml"
    config.write_text(
        f"""
mcp:
  enabled: true
  auto_evict: true
  max_failures_before_evict: 2
  servers:
    - name: http_stub
      transport: http
      url: {mcp_http_server}
""",
        encoding="utf-8",
    )
    import app.services.mcp_manager as mgr_module

    settings = Settings(str(config))
    monkeypatch.setattr(mgr_module, "settings", settings)
    reset_mcp_manager()
    manager = get_mcp_manager()
    manager.probe_all()
    assert manager.is_active("http_stub")
    manager._failures["http_stub"] = 2
    evicted = manager.evict_unhealthy(max_failures=2)
    assert "http_stub" in evicted
