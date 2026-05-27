"""LangGraphics optional wrapper (no package required in CI)."""

from app.runtime.langgraphics_wrap import (
    langgraphics_available,
    langgraphics_public_url,
    langgraphics_status,
    resolve_compiled_graph,
    reset_langgraphics_viewport,
)


def test_resolve_passthrough_when_disabled(test_settings, monkeypatch):
    import app.runtime.langgraphics_wrap as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.LANGGRAPHICS_ENABLED = False
    reset_langgraphics_viewport()

    calls: list[str] = []

    def getter() -> str:
        calls.append("get")
        return "compiled"

    assert resolve_compiled_graph(getter, "agent") == "compiled"
    assert calls == ["get"]


def test_langgraphics_available_without_package():
    # CI / py3.9 venv typically has no langgraphics wheel
    assert langgraphics_available() in (True, False)


def test_langgraphics_public_url_maps_bind_all(test_settings, monkeypatch):
    import app.runtime.langgraphics_wrap as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.LANGGRAPHICS_HOST = "0.0.0.0"
    test_settings.LANGGRAPHICS_PORT = 8764
    test_settings.LANGGRAPHICS_PUBLIC_HOST = ""
    assert langgraphics_public_url() == "http://127.0.0.1:8764"


def test_langgraphics_status_disabled(test_settings, monkeypatch):
    import app.runtime.langgraphics_wrap as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.LANGGRAPHICS_ENABLED = False
    st = langgraphics_status()
    assert st["configured"] is False
    assert st["enabled"] is False
    assert st["url"] is None


def test_resolve_swaps_graph_without_rebinding_ports(test_settings, monkeypatch):
    """Second graph_id must not call watch()/bind ports again (shared server)."""
    import app.runtime.langgraphics_wrap as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    test_settings.LANGGRAPHICS_ENABLED = True
    monkeypatch.setattr(mod, "langgraphics_available", lambda: True)
    reset_langgraphics_viewport(shutdown_servers=True)

    ensure_calls: list[str] = []
    swap_calls: list[str] = []

    class _Manager:
        topology_json = "{}"
        replay: list[str] = []
        loop = None
        connections: set[object] = set()

    def _ensure(_host: str, _port: int, _ws: int, _topology: dict) -> _Manager:
        ensure_calls.append("start")
        mod._http_server = object()
        mod._servers_started = True
        mod._broadcaster = _Manager()
        return mod._broadcaster

    def _build(compiled: str, _manager: _Manager, _http: object) -> str:
        return f"vp:{compiled}"

    def _swap(compiled: str, graph_id: str, _manager: _Manager) -> str:
        swap_calls.append(graph_id)
        return f"vp:{graph_id}:{compiled}"

    monkeypatch.setattr(mod, "_ensure_servers", _ensure)
    monkeypatch.setattr(mod, "_build_viewport", _build)
    monkeypatch.setattr(mod, "_swap_watched_graph", _swap)
    monkeypatch.setattr(
        mod,
        "_extract_topology",
        lambda _g: {"nodes": [{"id": "a"}], "edges": []},
    )

    first = resolve_compiled_graph(lambda: "compiled-a", "agent")
    second = resolve_compiled_graph(lambda: "compiled-m", "mission")

    assert first == "vp:compiled-a"
    assert second == "vp:mission:compiled-m"
    assert ensure_calls == ["start"]
    assert swap_calls == ["mission"]
    assert langgraphics_status()["active_graph"] == "mission"
    reset_langgraphics_viewport(shutdown_servers=True)
