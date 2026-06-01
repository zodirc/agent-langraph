from __future__ import annotations

from typing import Any, Iterator, Optional, cast

from langgraph.graph import END, StateGraph

from app.runtime.checkpointer import create_checkpointer
from app.runtime.langgraphics_wrap import resolve_compiled_graph
from app.runtime.graph_cache import cached_graph_compiler
from app.nodes.dead_letter_node import dead_letter_node
from app.nodes.human_review_node import human_review_node
from app.nodes.memory_writeback_node import memory_writeback_node
from app.nodes.output_guard_node import output_guard_node
from app.nodes.output_node import output_node
from app.nodes.planning_node import planning_node
from app.nodes.policy_node import policy_node
from app.nodes.reasoning_node import reasoning_node
from app.nodes.reflection_node import reflection_node
from app.nodes.react_deliberate_node import react_deliberate_node
from app.nodes.react_execute_node import react_execute_node
from app.nodes.react_finalize_node import react_finalize_node
from app.nodes.react_observe_node import react_observe_node
from app.nodes.rejected_node import rejected_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.nodes.writing_node import writing_node
from app.runtime.react_router import (
    route_after_react_deliberate,
    route_after_react_execute,
    route_after_react_finalize,
    route_after_react_observe,
)
from app.runtime.router import (
    route_after_output_guard,
    route_after_planning,
    route_after_policy_to_guard,
    route_after_reasoning,
    route_after_reflection,
    route_after_retrieval,
    route_after_tool,
    route_after_writing,
)
from app.runtime.state import AgentState, append_node_history, ensure_agent_state, merge_state
from app.services.session_turn import graph_thread_id


def build_agent_graph() -> StateGraph:
    """Build Agent execution graph per architecture §6.3."""
    workflow = StateGraph(AgentState)

    workflow.add_node("planning", planning_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("tool_execution", tool_execution_node)
    workflow.add_node("writing", writing_node)
    workflow.add_node("reasoning", reasoning_node)
    workflow.add_node("reflection", reflection_node)
    workflow.add_node("policy", policy_node)
    workflow.add_node("output_guard", output_guard_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("rejected", rejected_node)
    workflow.add_node("dead_letter", dead_letter_node)
    workflow.add_node("output", output_node)
    workflow.add_node("memory_writeback", memory_writeback_node)
    workflow.add_node("react_deliberate", react_deliberate_node)
    workflow.add_node("react_execute", react_execute_node)
    workflow.add_node("react_observe", react_observe_node)
    workflow.add_node("react_finalize", react_finalize_node)

    workflow.set_entry_point("planning")

    workflow.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "retrieval": "retrieval",
            "tool_execution": "tool_execution",
            "writing": "writing",
            "reasoning": "reasoning",
            "react_deliberate": "react_deliberate",
            "planning": "planning",
            "dead_letter": "dead_letter",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "react_deliberate",
        route_after_react_deliberate,
        {
            "react_execute": "react_execute",
            "react_finalize": "react_finalize",
            "reflection": "reflection",
            "reasoning": "reasoning",
        },
    )
    workflow.add_edge("react_execute", "react_observe")
    workflow.add_conditional_edges(
        "react_observe",
        route_after_react_observe,
        {
            "react_deliberate": "react_deliberate",
            "react_finalize": "react_finalize",
            "planning": "planning",
            "reflection": "reflection",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "react_finalize",
        route_after_react_finalize,
        {
            "reasoning": "reasoning",
            "planning": "planning",
            "reflection": "reflection",
            "human_review": "human_review",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "retrieval",
        route_after_retrieval,
        {
            "tool_execution": "tool_execution",
            "writing": "writing",
            "reasoning": "reasoning",
            "retrieval": "retrieval",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "tool_execution",
        route_after_tool,
        {
            "tool_execution": "tool_execution",
            "writing": "writing",
            "reasoning": "reasoning",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "writing",
        route_after_writing,
        {
            "writing": "writing",
            "reasoning": "reasoning",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "reasoning",
        route_after_reasoning,
        {
            "reasoning": "reasoning",
            "reflection": "reflection",
            "policy": "policy",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "reflection",
        route_after_reflection,
        {
            "reasoning": "reasoning",
            "planning": "planning",
            "policy": "policy",
        },
    )

    workflow.add_conditional_edges(
        "policy",
        route_after_policy_to_guard,
        {
            "output_guard": "output_guard",
            "output": "output",
            "human_review": "human_review",
            "rejected": "rejected",
        },
    )
    workflow.add_conditional_edges(
        "output_guard",
        route_after_output_guard,
        {
            "output": "output",
            "rejected": "rejected",
        },
    )
    workflow.add_edge("rejected", END)
    workflow.add_edge("dead_letter", END)
    workflow.add_edge("human_review", "output_guard")
    workflow.add_edge("output", "memory_writeback")
    workflow.add_edge("memory_writeback", END)

    return workflow


def _track_node_history(state: AgentState, node_name: str) -> AgentState:
    return append_node_history(state, node_name)


def _compile_agent_graph(workflow: StateGraph) -> Any:
    checkpointer = create_checkpointer()
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_review"],
    )


get_compiled_graph = cached_graph_compiler(build_agent_graph, compile_fn=_compile_agent_graph)


def run_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> AgentState:
    """Execute graph until completion or human-review interrupt."""
    graph = resolve_compiled_graph(get_compiled_graph, "agent")
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    result = graph.invoke(state, config)
    if isinstance(result, dict):
        return ensure_agent_state(result)
    return cast(AgentState, result)


def resume_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> AgentState:
    """Resume graph after human review feedback is attached."""
    graph = resolve_compiled_graph(get_compiled_graph, "agent")
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    result = graph.invoke(state, config)
    if isinstance(result, dict):
        return ensure_agent_state(result)
    return cast(AgentState, result)


def stream_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> Iterator[tuple[str, AgentState]]:
    """Yield (node_name, state_snapshot) for each completed node."""
    graph = resolve_compiled_graph(get_compiled_graph, "agent")
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    latest: AgentState = state

    for chunk in graph.stream(state, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                latest = merge_state(latest, **update)
            latest = _track_node_history(latest, node_name)
            yield node_name, latest
