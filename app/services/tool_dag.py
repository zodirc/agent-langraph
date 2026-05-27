"""
Tool execution DAG — parallel stages with dependency order.

Planning may set:
  tool_stages: [["calculator", "get_runtime_info"], ["echo"]]
or
  tool_dag: { "nodes": [{"id":"calc","tool":"calculator","params":{}}], ...], "edges": [] }
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from app.config.settings import settings
from app.runtime.state import AgentState


def parse_tool_stages(payload: dict[str, Any], selected_tools: list[str]) -> list[list[str]]:
    """Resolve execution stages from payload or fall back to sequential single-tool stages."""
    raw = payload.get("tool_stages")
    if isinstance(raw, list) and raw:
        stages: list[list[str]] = []
        for stage in raw:
            if isinstance(stage, list):
                names = [str(t) for t in stage if str(t)]
            else:
                names = [str(stage)]
            if names:
                stages.append(names)
        if stages:
            return stages

    dag = payload.get("tool_dag")
    if isinstance(dag, dict) and dag.get("nodes"):
        return _stages_from_dag(dag, selected_tools)

    return [[name] for name in selected_tools]


def _stages_from_dag(dag: dict[str, Any], selected_tools: list[str]) -> list[list[str]]:
    """Topological layers from explicit DAG."""
    nodes: dict[str, dict[str, Any]] = {}
    for item in dag.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        nid = str(item.get("id") or item.get("tool") or "")
        tool = str(item.get("tool") or nid)
        if tool:
            nodes[nid or tool] = item

    edges: list[tuple[str, str]] = []
    for edge in dag.get("edges") or []:
        if isinstance(edge, dict):
            edges.append((str(edge.get("from")), str(edge.get("to"))))
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
            edges.append((str(edge[0]), str(edge[1])))

    if not nodes:
        return [[name] for name in selected_tools]

    id_to_tool = {nid: str(n.get("tool") or nid) for nid, n in nodes.items()}
    incoming: dict[str, set[str]] = {nid: set() for nid in nodes}
    for src, dst in edges:
        if dst in incoming and src in nodes:
            incoming[dst].add(src)

    remaining = set(nodes.keys())
    stages: list[list[str]] = []
    while remaining:
        ready = [nid for nid in remaining if not (incoming[nid] & remaining)]
        if not ready:
            ready = [next(iter(remaining))]
        tool_names = [id_to_tool[nid] for nid in ready if id_to_tool.get(nid)]
        if tool_names:
            stages.append(tool_names)
        remaining -= set(ready)

    return stages or [[name] for name in selected_tools]


def execute_tool_stages(
    state: AgentState,
    stages: list[list[str]],
    *,
    invoke_fn: Callable[[str, AgentState], dict[str, Any]],
    initial_results: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Run each stage in order; tools within a stage run in parallel."""
    results: list[dict[str, Any]] = list(initial_results or [])
    max_workers = max(1, int(getattr(settings, "TOOL_EXEC_MAX_WORKERS", 4)))

    for stage_index, tools in enumerate(stages):
        if not tools:
            continue
        if len(tools) == 1:
            name = tools[0]
            try:
                results.append(invoke_fn(name, state))
            except Exception as exc:
                results.append(_error_outcome(name, exc))
            continue

        with ThreadPoolExecutor(max_workers=min(max_workers, len(tools))) as pool:
            futures = {pool.submit(invoke_fn, name, state): name for name in tools}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(_error_outcome(name, exc))

    return results


def _error_outcome(tool_name: str, exc: Exception) -> dict[str, Any]:
    return {"tool": tool_name, "status": "error", "error": str(exc)}
