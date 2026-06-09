"""Supervisor 多域分解图。

supervisor_decompose → supervisor_worker → supervisor_merge → policy → output。
graph_runner mode=supervisor；API POST /supervisor/tasks/stream。

Multi-domain supervisor StateGraph with worker subtasks and shared output tail.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, cast

from langgraph.graph import END, StateGraph

from app.runtime.checkpointer import create_checkpointer
from app.runtime.graph_cache import cached_graph_compiler
from app.nodes.human_review_node import human_review_node
from app.nodes.memory_writeback_node import memory_writeback_node
from app.nodes.output_node import output_node
from app.nodes.policy_node import policy_node
from app.nodes.rejected_node import rejected_node
from app.nodes.supervisor_decompose_node import supervisor_decompose_node
from app.nodes.supervisor_merge_node import supervisor_merge_node
from app.nodes.supervisor_worker_node import supervisor_worker_node
from app.runtime.router import route_after_policy
from app.runtime.state import AgentState, append_node_history, ensure_agent_state, merge_state


def build_supervisor_graph() -> StateGraph:
    """Supervisor-Worker graph per architecture §20."""
    workflow = StateGraph(AgentState)

    workflow.add_node("supervisor_decompose", supervisor_decompose_node)
    workflow.add_node("supervisor_worker", supervisor_worker_node)
    workflow.add_node("supervisor_merge", supervisor_merge_node)
    workflow.add_node("policy", policy_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("rejected", rejected_node)
    workflow.add_node("output", output_node)
    workflow.add_node("memory_writeback", memory_writeback_node)

    workflow.set_entry_point("supervisor_decompose")
    workflow.add_edge("supervisor_decompose", "supervisor_worker")
    workflow.add_edge("supervisor_worker", "supervisor_merge")
    workflow.add_edge("supervisor_merge", "policy")

    workflow.add_conditional_edges(
        "policy",
        route_after_policy,
        {
            "output": "output",
            "human_review": "human_review",
            "rejected": "rejected",
        },
    )
    workflow.add_edge("rejected", END)
    workflow.add_edge("human_review", "output")
    workflow.add_edge("output", END)

    return workflow


def _compile_supervisor(workflow: Any) -> Any:
    return workflow.compile(
        checkpointer=create_checkpointer(),
        interrupt_before=["human_review"],
    )


get_compiled_supervisor_graph = cached_graph_compiler(
    build_supervisor_graph, compile_fn=_compile_supervisor
)


def resume_supervisor_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> AgentState:
    graph = get_compiled_supervisor_graph()
    config = {"configurable": {"thread_id": thread_id or state["task_id"]}}
    result = graph.invoke(state, config)
    if isinstance(result, dict):
        return ensure_agent_state(result)
    return cast(AgentState, result)


def run_supervisor_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> AgentState:
    graph = get_compiled_supervisor_graph()
    config = {"configurable": {"thread_id": thread_id or state["task_id"]}}
    result = graph.invoke(state, config)
    if isinstance(result, dict):
        return ensure_agent_state(result)
    return cast(AgentState, result)


def stream_supervisor_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> Iterator[tuple[str, AgentState]]:
    """Yield (node_name, state_snapshot) for supervisor graph execution."""
    graph = get_compiled_supervisor_graph()
    config = {"configurable": {"thread_id": thread_id or state["task_id"]}}
    latest: AgentState = state

    for chunk in graph.stream(state, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                latest = merge_state(latest, **update)
            latest = append_node_history(latest, node_name)
            yield node_name, latest
