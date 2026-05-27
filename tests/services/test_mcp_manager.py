import sys

from app.config.settings import Settings
from app.services.mcp_manager import (
    MCPServerManager,
    get_mcp_manager,
    list_mcp_prompts,
    list_mcp_resources,
    reset_mcp_manager,
)


def test_probe_and_evict_after_failures(monkeypatch):
    reset_mcp_manager()
    manager = MCPServerManager()
    manager._failures["bad"] = 3
    manager._health["bad"] = False
    evicted = manager.evict_unhealthy(max_failures=3)
    assert "bad" in evicted
    assert not manager.is_active("bad")


def test_restore_server_clears_eviction(monkeypatch):
    manager = MCPServerManager()
    manager._evicted.add("stub")
    monkeypatch.setattr(
        "app.services.mcp_manager._probe_single_server",
        lambda _server: True,
    )
    monkeypatch.setattr(
        "app.services.mcp_manager.settings.MCP_SERVERS",
        [{"name": "stub", "transport": "stdio"}],
    )
    assert manager.restore_server("stub") is True
    assert manager.is_active("stub")


def test_list_mcp_resources_stdio(tmp_path, monkeypatch):
    config = tmp_path / "cfg.yaml"
    config.write_text(
        f"""
mcp:
  enabled: true
  servers:
    - name: stub
      transport: stdio
      command: {sys.executable}
      args: ["-m", "app.mcp_stubs.stdio_server"]
""",
        encoding="utf-8",
    )
    import app.services.mcp_manager as mcp_mgr
    import app.services.mcp_bridge as mcp_bridge

    settings = Settings(str(config))
    monkeypatch.setattr(mcp_mgr, "settings", settings)
    monkeypatch.setattr(mcp_bridge, "settings", settings)
    server = settings.MCP_SERVERS[0]
    resources = list_mcp_resources(server)
    assert len(resources) >= 1
    prompts = list_mcp_prompts(server)
    assert len(prompts) >= 1


def test_register_skips_evicted_server(tmp_path, monkeypatch):
    config = tmp_path / "cfg.yaml"
    config.write_text(
        f"""
mcp:
  enabled: true
  auto_evict: false
  register_resources: false
  servers:
    - name: stub
      transport: stdio
      command: {sys.executable}
      args: ["-m", "app.mcp_stubs.stdio_server"]
      prefix: mcp
""",
        encoding="utf-8",
    )
    import app.services.mcp_bridge as mcp_module
    import app.services.mcp_manager as mgr_module

    settings = Settings(str(config))
    monkeypatch.setattr(mcp_module, "settings", settings)
    monkeypatch.setattr(mgr_module, "settings", settings)
    reset_mcp_manager()
    manager = get_mcp_manager()
    manager._evicted.add("stub")

    from app.services.tool_registry import ToolRegistry

    registry = ToolRegistry()
    count = mcp_module.register_mcp_tools(registry)
    assert count == 0
