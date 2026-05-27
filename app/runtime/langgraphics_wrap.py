"""
Optional LangGraphics integration — live LangGraph execution visualization.

Upstream: https://github.com/proactive-agent/langgraphics
Requires Python >=3.10 and ``langgraphics`` package (PyPI or git install).

Uses a single HTTP/WebSocket server pair per process and swaps the watched graph
when runtime switches (agent ↔ mission), so port 8764/8765 is not re-bound.

Uses sync ``CompiledGraph.stream()`` (not Viewport's async path) so PostgreSQL
checkpointer works — upstream Viewport.astream calls aget_tuple → NotImplementedError.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Iterator, Optional

from app.config.settings import settings

logger = logging.getLogger(__name__)

_active_id: Optional[str] = None
_active_graph: Any = None
_servers_started: bool = False
_http_server: Any = None
_broadcaster: Any = None


def langgraphics_available() -> bool:
    try:
        import langgraphics  # noqa: F401

        return True
    except ImportError:
        return False


def langgraphics_enabled() -> bool:
    return bool(getattr(settings, "LANGGRAPHICS_ENABLED", False)) and langgraphics_available()


def langgraphics_public_url() -> str:
    """Browser-openable URL (maps 0.0.0.0 bind address to localhost or public host)."""
    public = str(getattr(settings, "LANGGRAPHICS_PUBLIC_HOST", "") or "").strip()
    if public:
        if public.startswith("http://") or public.startswith("https://"):
            return public.rstrip("/")
        port = int(getattr(settings, "LANGGRAPHICS_PORT", 8764))
        return f"http://{public}:{port}"
    host = str(getattr(settings, "LANGGRAPHICS_HOST", "127.0.0.1"))
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = int(getattr(settings, "LANGGRAPHICS_PORT", 8764))
    return f"http://{host}:{port}"


def langgraphics_status() -> dict[str, Any]:
    """Health / SSE payload for UI."""
    configured = bool(getattr(settings, "LANGGRAPHICS_ENABLED", False))
    installed = langgraphics_available()
    active = langgraphics_enabled()
    return {
        "configured": configured,
        "package_installed": installed,
        "enabled": active,
        "url": langgraphics_public_url() if active else None,
        "active_graph": _active_id,
        "servers_started": _servers_started,
        "hint": (
            "任务执行时打开 url 查看节点高亮；Docker 需 LANGGRAPHICS_ENABLED=true 并映射 8764/8765"
            if not active and installed
            else None
        ),
    }


def _extract_topology(compiled: Any) -> dict[str, Any]:
    from langgraphics.topology import extract

    from typing import cast

    return cast(dict[str, Any], extract(compiled))


def _edge_lookup(topology: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(e["source"], e["target"]): e["id"] for e in topology.get("edges", [])}


def _run_on_broadcaster_loop(broadcaster: Any, coro: Any, *, timeout: float = 5.0) -> None:
    loop = broadcaster.loop
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
    except Exception as exc:
        logger.debug("LangGraphics async broadcast skipped: %s", exc)


def _broadcast_topology(broadcaster: Any) -> None:
    """Push a new graph topology to all connected LangGraphics clients."""

    async def _send() -> None:
        if not broadcaster.connections:
            return
        payload = broadcaster.topology_json
        await asyncio.gather(
            *[conn.send(payload) for conn in list(broadcaster.connections)],
            return_exceptions=True,
        )

    _run_on_broadcaster_loop(broadcaster, _send())


def _ensure_servers(host: str, port: int, ws_port: int, topology: dict[str, Any]) -> Any:
    """Start HTTP + WS servers once; return Broadcaster."""
    global _servers_started, _http_server, _broadcaster

    from langgraphics.broadcaster import Broadcaster
    from langgraphics.watch import start_http_server, start_ws_server

    if _servers_started and _broadcaster is not None:
        return _broadcaster

    manager = Broadcaster(topology)
    _http_server = start_http_server(host, port)
    start_ws_server(manager, host, ws_port)
    _broadcaster = manager
    _servers_started = True
    return manager


class SyncLangGraphViewport:
    """
    LangGraphics viewport that uses sync ``graph.stream`` / ``invoke``.

    Upstream ``langgraphics.streamer.Viewport.stream`` drives ``astream``, which
    requires async checkpointer APIs our Postgres saver does not implement.
    """

    def __init__(
        self,
        graph: Any,
        broadcaster: Any,
        edge_lookup: dict[tuple[str, str], str],
        http_server: Any,
    ) -> None:
        self.graph = graph
        self.ws = broadcaster
        self.edge_lookup = edge_lookup
        self.http_server = http_server
        self.node_current: Optional[str] = None
        self.node_names: set[str] = set()
        self.predecessors: dict[str, set[str]] = {}
        for src, tgt in edge_lookup:
            self.node_names.add(src)
            self.node_names.add(tgt)
            self.predecessors.setdefault(tgt, set()).add(src)
        self.node_names -= {"__start__", "__end__"}
        self.generation: dict[str, int] = {"__start__": 0}
        self.linked: set[tuple[str, int, str]] = set()

    def _make_config(self, config: Any) -> dict[str, Any]:
        from langgraphics.streamer import BroadcastingTracer

        tracer = BroadcastingTracer(self)
        merged: dict[str, Any] = dict(config or {})
        merged["callbacks"] = list(merged.get("callbacks") or []) + [tracer]
        return merged

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Called by LangGraphics BroadcastingTracer (async)."""
        await self._broadcast_async(message)

    def _broadcast_sync(self, message: dict[str, Any]) -> None:
        _run_on_broadcaster_loop(self.ws, self.broadcast(message))

    async def _broadcast_async(self, message: dict[str, Any]) -> None:
        message_str = json.dumps(message)
        self.ws.record(message_str)
        if self.ws.loop is None:
            return
        await self.ws.broadcast(message_str)

    async def _emit_edge(self, target: str) -> None:
        for source in self.predecessors.get(target, set()):
            if (src_gen := self.generation.get(source)) is None:
                continue
            if (key := (source, src_gen, target)) in self.linked:
                continue
            self.linked.add(key)
            if edge_id := self.edge_lookup.get((source, target)):
                await self._broadcast_async(
                    {
                        "type": "edge_active",
                        "source": source,
                        "target": target,
                        "edge_id": edge_id,
                    }
                )
        self.generation[target] = self.generation.get(target, -1) + 1

    def _emit_edge_sync(self, target: str) -> None:
        _run_on_broadcaster_loop(self.ws, self._emit_edge(target))

    async def _emit_error(self, last_node: str) -> None:
        for (src, tgt), eid in self.edge_lookup.items():
            if src == last_node and tgt == self.node_current:
                await self._broadcast_async(
                    {
                        "type": "error",
                        "edge_id": eid,
                        "source": last_node,
                        "target": self.node_current,
                    }
                )
                break

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Iterator[Any]:
        run_id = uuid.uuid4().hex[:8]
        self._broadcast_sync({"type": "run_start", "run_id": run_id})
        last_node = "__start__"
        merged_config = self._make_config(config)
        stream_mode = kwargs.get("stream_mode", "values")

        try:
            for chunk in self.graph.stream(input, config=merged_config, **kwargs):
                if isinstance(chunk, dict) and stream_mode == "updates":
                    for node_name in chunk:
                        if node_name == "__metadata__":
                            continue
                        self._emit_edge_sync(node_name)
                        last_node = node_name
                yield chunk

            if len(self.generation) > 1:
                self._emit_edge_sync("__end__")
            self._broadcast_sync({"type": "run_end", "run_id": run_id})
        except Exception:
            self.node_current = last_node
            _run_on_broadcaster_loop(self.ws, self._emit_error(last_node))
            raise

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self.graph.invoke(input, config=self._make_config(config), **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.graph, name)


def _build_viewport(compiled: Any, manager: Any, http_server: Any) -> SyncLangGraphViewport:
    topology = _extract_topology(compiled)
    return SyncLangGraphViewport(
        compiled,
        manager,
        _edge_lookup(topology),
        http_server,
    )


def _swap_watched_graph(compiled: Any, graph_id: str, manager: Any) -> SyncLangGraphViewport:
    """Update shared broadcaster topology and attach a new Viewport."""
    topology = _extract_topology(compiled)
    manager.topology_json = json.dumps(topology)
    manager.replay = []
    _broadcast_topology(manager)
    viewport = _build_viewport(compiled, manager, _http_server)
    logger.info(
        "LangGraphics switched to graph=%s (%d nodes)",
        graph_id,
        len(topology.get("nodes", [])),
    )
    return viewport


def resolve_compiled_graph(getter: Callable[[], Any], graph_id: str) -> Any:
    """
    Return compiled graph, optionally wrapped for LangGraphics live UI.

    One HTTP/WS server per process; switching graph_id rebroadcasts topology instead
    of calling ``watch()`` again (avoids 'Address already in use' and empty UI).
    """
    global _active_id, _active_graph

    compiled = getter()
    if not langgraphics_enabled():
        return compiled

    if _active_id == graph_id and _active_graph is not None:
        _active_graph.graph = compiled
        return _active_graph

    if not langgraphics_available():
        logger.warning(
            "LANGGRAPHICS_ENABLED=true but langgraphics not installed "
            "(pip install 'langgraphics>=0.1.0b3' or Python>=3.10)"
        )
        return compiled

    host = str(getattr(settings, "LANGGRAPHICS_HOST", "127.0.0.1"))
    port = int(getattr(settings, "LANGGRAPHICS_PORT", 8764))
    ws_port = int(getattr(settings, "LANGGRAPHICS_WS_PORT", 8765))
    open_browser = bool(getattr(settings, "LANGGRAPHICS_OPEN_BROWSER", True))

    try:
        if not _servers_started:
            topology = _extract_topology(compiled)
            manager = _ensure_servers(host, port, ws_port, topology)
            _active_graph = _build_viewport(compiled, manager, _http_server)
            _active_id = graph_id
            if open_browser:
                import webbrowser

                webbrowser.open(langgraphics_public_url())
            logger.info(
                "LangGraphics watching graph=%s at http://%s:%s",
                graph_id,
                host if host not in ("0.0.0.0", "::") else "127.0.0.1",
                port,
            )
        else:
            assert _broadcaster is not None and _http_server is not None
            _active_graph = _swap_watched_graph(compiled, graph_id, _broadcaster)
            _active_id = graph_id
        return _active_graph
    except Exception as exc:
        logger.warning("LangGraphics setup failed for %s: %s", graph_id, exc)
        return compiled


def reset_langgraphics_viewport(*, shutdown_servers: bool = False) -> None:
    """Clear cached viewport; optionally stop shared HTTP/WS servers."""
    global _active_id, _active_graph, _servers_started, _http_server, _broadcaster

    _active_id = None
    _active_graph = None
    if not shutdown_servers or not _servers_started:
        return

    if _broadcaster is not None:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_broadcaster.shutdown())
            loop.close()
        except Exception as exc:
            logger.debug("LangGraphics broadcaster shutdown: %s", exc)
    if _http_server is not None:
        try:
            _http_server.shutdown()
        except Exception as exc:
            logger.debug("LangGraphics http shutdown: %s", exc)

    _servers_started = False
    _http_server = None
    _broadcaster = None
