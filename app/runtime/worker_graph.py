"""Worker 子图：无 planning，从 retrieval 起，复用主图 tool 路由。

由 worker_executor 调用；reasoning 在 supervisor_merge 汇总。

Per-subtask worker graph: retrieval → tool → reasoning without planning.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.runtime.checkpointer import create_checkpointer
from app.runtime.graph_cache import cached_graph_compiler
from app.nodes.context_governance_node import context_governance_node
from app.nodes.reasoning_node import reasoning_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.runtime.router import route_after_retrieval, route_after_tool
from app.runtime.state import AgentState, append_node_history, ensure_agent_state, merge_state


def build_worker_graph() -> StateGraph:
    """Domain Worker subgraph per architecture §20.3."""
    workflow = StateGraph(AgentState)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("tool_execution", tool_execution_node)
    workflow.add_node("context_governance", context_governance_node)
    workflow.add_node("reasoning", reasoning_node)
    workflow.set_entry_point("retrieval")
    workflow.add_conditional_edges(
        "retrieval",
        route_after_retrieval,
        {
            "tool_execution": "tool_execution",
            "context_governance": "context_governance",
            "retrieval": "retrieval",
            "dead_letter": "reasoning",
        },
    )
    workflow.add_conditional_edges(
        "tool_execution",
        route_after_tool,
        {
            "tool_execution": "tool_execution",
            "context_governance": "context_governance",
            "dead_letter": "reasoning",
        },
    )
    workflow.add_edge("context_governance", "reasoning")
    workflow.add_edge("reasoning", END)
    return workflow


def _compile_worker(workflow: Any) -> Any:
    return workflow.compile(checkpointer=create_checkpointer())


get_compiled_worker_graph = cached_graph_compiler(build_worker_graph, compile_fn=_compile_worker)


def run_worker_graph(state: AgentState) -> AgentState:
    """Execute worker subgraph for a single subtask."""
    graph = get_compiled_worker_graph()
    config = {"configurable": {"thread_id": state["task_id"]}}
    latest = state
    for chunk in graph.stream(state, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                latest = merge_state(latest, **update)
            latest = append_node_history(latest, node_name)
    return latest
