from __future__ import annotations

from typing import Any, Iterator, Optional, cast

from langgraph.graph import END, StateGraph

from app.nodes.dead_letter_node import dead_letter_node
from app.nodes.exploration_nodes import (
    explore_finalize_node,
    explore_hypothesize_node,
    explore_init_node,
    explore_prune_node,
    explore_probe_node,
    explore_score_node,
)
from app.nodes.human_review_node import human_review_node
from app.nodes.memory_writeback_node import memory_writeback_node
from app.nodes.output_node import output_node
from app.nodes.policy_node import policy_node
from app.nodes.rejected_node import rejected_node
from app.runtime.checkpointer import create_checkpointer
from app.runtime.graph_cache import cached_graph_compiler
from app.runtime.exploration_router import route_after_explore_prune
from app.runtime.router import route_after_policy
from app.runtime.state import AgentState, append_node_history, ensure_agent_state, merge_state
from app.services.session_turn import graph_thread_id


def build_exploration_graph() -> StateGraph:
    """Exploration runtime for analysis domain (Ch21 pilot)."""
    workflow = StateGraph(AgentState)

    workflow.add_node("explore_init", explore_init_node)
    workflow.add_node("explore_hypothesize", explore_hypothesize_node)
    workflow.add_node("explore_probe", explore_probe_node)
    workflow.add_node("explore_score", explore_score_node)
    workflow.add_node("explore_prune", explore_prune_node)
    workflow.add_node("explore_finalize", explore_finalize_node)
    workflow.add_node("policy", policy_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("rejected", rejected_node)
    workflow.add_node("dead_letter", dead_letter_node)
    workflow.add_node("output", output_node)
    workflow.add_node("memory_writeback", memory_writeback_node)

    workflow.set_entry_point("explore_init")
    workflow.add_edge("explore_init", "explore_hypothesize")
    workflow.add_edge("explore_hypothesize", "explore_probe")
    workflow.add_edge("explore_probe", "explore_score")
    workflow.add_edge("explore_score", "explore_prune")
    workflow.add_conditional_edges(
        "explore_prune",
        route_after_explore_prune,
        {
            "hypothesize": "explore_hypothesize",
            "finalize": "explore_finalize",
        },
    )
    workflow.add_edge("explore_finalize", "policy")
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
    workflow.add_edge("dead_letter", END)
    workflow.add_edge("human_review", "output")
    workflow.add_edge("output", "memory_writeback")
    workflow.add_edge("memory_writeback", END)

    return workflow


def _compile_exploration(workflow: Any) -> Any:
    return workflow.compile(
        checkpointer=create_checkpointer(),
        interrupt_before=["human_review"],
    )


get_compiled_exploration_graph = cached_graph_compiler(
    build_exploration_graph, compile_fn=_compile_exploration
)


def run_exploration_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> AgentState:
    graph = get_compiled_exploration_graph()
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    result = graph.invoke(state, config)
    if isinstance(result, dict):
        return ensure_agent_state(result)
    return cast(AgentState, result)


def stream_exploration_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> Iterator[tuple[str, AgentState]]:
    graph = get_compiled_exploration_graph()
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    latest: AgentState = state
    for chunk in graph.stream(state, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                latest = merge_state(latest, **update)
            latest = append_node_history(latest, node_name)
            yield node_name, latest
