import sys

from app.config.settings import Settings
from app.services.mcp_bridge import register_mcp_tools
from app.services.tool_registry import ToolRegistry


def test_register_stdio_mcp_tools(tmp_path, monkeypatch):
    config = tmp_path / "cfg.yaml"
    config.write_text(
        f"""
mcp:
  enabled: true
  timeout: 10
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

    settings = Settings(str(config))
    monkeypatch.setattr(mcp_module, "settings", settings)

    registry = ToolRegistry()
    count = register_mcp_tools(registry)
    assert count >= 2
    assert "mcp_ping" in registry.list_tools()
    result = registry.invoke("mcp_ping", {"message": "hello"}, user_role="user")
    assert result["result"]["pong"] == "hello"
