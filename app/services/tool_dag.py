"""工具 DAG 执行
规划输入 Planning sets (planning_node → input_payload)
  tool_stages: [["calc","read"], ["write"]]  — 显式阶段，同阶段并行
  tool_dag: { nodes:[{id,tool,params}], edges:[{from,to}] } — 拓扑分层 → stages
  优先 tool_stages → 否则 _stages_from_dag → 否则每工具单阶段 [[t],...]
  顺序执行各 stage；stage 内 ThreadPoolExecutor 并行 (TOOL_EXEC_MAX_WORKERS)
  invoke_fn 由 tool_execution_node._invoke 提供（含 intent_guard、registry.invoke）
调用方 Caller: tool_execution_node only.

Tool execution DAG — ordered stages, parallel within stage.
parse_tool_stages(payload, selected_tools)
execute_tool_stages(stages, invoke_fn)"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from app.config.settings import settings
from app.runtime.state import AgentState
from app.services.tool_side_effect import normalize_stages_for_safe_parallel, tool_is_read_only


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
            return normalize_stages_for_safe_parallel(stages)

    dag = payload.get("tool_dag")
    if isinstance(dag, dict) and dag.get("nodes"):
        return normalize_stages_for_safe_parallel(_stages_from_dag(dag, selected_tools))

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
    execution_version: int | None = None,
) -> list[dict[str, Any]]:
    """Run each stage in order; read-only tools within a stage may run in parallel."""
    results: list[dict[str, Any]] = list(initial_results or [])
    max_workers = max(1, int(getattr(settings, "TOOL_EXEC_MAX_WORKERS", 4)))
    version = execution_version if execution_version is not None else state.get("execution_version")

    for stage_index, tools in enumerate(stages):
        if not tools:
            continue
        parallel_ok = len(tools) > 1 and all(tool_is_read_only(t) for t in tools)
        if len(tools) == 1 or not parallel_ok:
            for name in tools:
                try:
                    row = invoke_fn(name, state)
                    if isinstance(row, dict):
                        row = {**row, "execution_version": version, "observation_only": True}
                    results.append(row)
                except Exception as exc:
                    results.append(_error_outcome(name, exc, execution_version=version))
            continue

        with ThreadPoolExecutor(max_workers=min(max_workers, len(tools))) as pool:
            futures = {pool.submit(invoke_fn, name, state): name for name in tools}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    row = future.result()
                    if isinstance(row, dict):
                        row = {**row, "execution_version": version, "observation_only": True}
                    results.append(row)
                except Exception as exc:
                    results.append(_error_outcome(name, exc, execution_version=version))

    return results


def _error_outcome(tool_name: str, exc: Exception, *, execution_version: int | None = None) -> dict[str, Any]:
    out = {"tool": tool_name, "status": "error", "error": str(exc)}
    if execution_version is not None:
        out["execution_version"] = execution_version
    return out
