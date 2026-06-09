"""主 Agent 图（LangGraph StateGraph）。

Frozen spine (optimization_execution_plan §2.1):
event_classification → acknowledge → interrupt_control → incremental_planning
→ retrieval/tool/engineering → context_governance → reasoning_or_writing
→ verification → policy → output → END (memory/eval async via close_turn_async)
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, cast

from langgraph.graph import END, StateGraph

from app.runtime.checkpointer import create_checkpointer
from app.runtime.graph_cache import cached_graph_compiler
from app.nodes.acknowledge_node import acknowledge_node
from app.nodes.context_governance_node import context_governance_node
from app.nodes.dead_letter_node import dead_letter_node
from app.nodes.engineering_node import engineering_execution_node
from app.nodes.event_classification_node import event_classification_node
from app.nodes.human_review_node import human_review_node
from app.nodes.incremental_planning_node import incremental_planning_node
from app.nodes.interrupt_control_node import interrupt_control_node
from app.nodes.output_node import output_node
from app.nodes.policy_node import policy_node
from app.nodes.reasoning_or_writing_node import (
    reasoning_or_writing_node,
    route_after_reasoning_or_writing,
)
from app.nodes.rejected_node import rejected_node
from app.nodes.retrieval_node import retrieval_node
from app.nodes.tool_node import tool_execution_node
from app.nodes.verification_node import verification_node
from app.runtime.event_router import (
    route_after_acknowledge,
    route_after_event_classification,
)
from app.runtime.router import (
    route_after_engineering,
    route_after_policy,
    route_after_retrieval,
    route_after_tool,
)
from app.runtime.runtime_router import (
    route_after_context_governance,
    route_after_incremental_planning,
    route_after_interrupt_control,
    route_after_verification,
)
from app.runtime.state import AgentState, append_node_history, ensure_agent_state, merge_state
from app.services.session_turn import graph_thread_id


def build_agent_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    # --- Foreground / control ---
    workflow.add_node("event_classification", event_classification_node)
    workflow.add_node("acknowledge", acknowledge_node)
    workflow.add_node("interrupt_control", interrupt_control_node)
    workflow.add_node("incremental_planning", incremental_planning_node)

    # --- Execution ---
    workflow.add_node("engineering_execution", engineering_execution_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("tool_execution", tool_execution_node)
    workflow.add_node("context_governance", context_governance_node)
    workflow.add_node("reasoning_or_writing", reasoning_or_writing_node)

    # --- Verification / output ---
    workflow.add_node("verification", verification_node)
    workflow.add_node("policy", policy_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("rejected", rejected_node)
    workflow.add_node("dead_letter", dead_letter_node)
    workflow.add_node("output", output_node)

    workflow.set_entry_point("event_classification")

    workflow.add_conditional_edges(
        "event_classification",
        route_after_event_classification,
        {"acknowledge": "acknowledge"},
    )
    workflow.add_conditional_edges(
        "acknowledge",
        route_after_acknowledge,
        {"interrupt_control": "interrupt_control"},
    )
    workflow.add_conditional_edges(
        "interrupt_control",
        route_after_interrupt_control,
        {
            "incremental_planning": "incremental_planning",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "incremental_planning",
        route_after_incremental_planning,
        {
            "retrieval": "retrieval",
            "tool_execution": "tool_execution",
            "context_governance": "context_governance",
            "reasoning_or_writing": "reasoning_or_writing",
            "engineering_execution": "engineering_execution",
            "incremental_planning": "incremental_planning",
            "dead_letter": "dead_letter",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "engineering_execution",
        route_after_engineering,
        {
            "policy": "policy",
            "engineering_execution": "engineering_execution",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "retrieval",
        route_after_retrieval,
        {
            "tool_execution": "tool_execution",
            "context_governance": "context_governance",
            "retrieval": "retrieval",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "tool_execution",
        route_after_tool,
        {
            "tool_execution": "tool_execution",
            "context_governance": "context_governance",
            "incremental_planning": "incremental_planning",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "context_governance",
        route_after_context_governance,
        {"reasoning_or_writing": "reasoning_or_writing"},
    )
    workflow.add_conditional_edges(
        "reasoning_or_writing",
        route_after_reasoning_or_writing,
        {
            "reasoning_or_writing": "reasoning_or_writing",
            "verification": "verification",
            "rejected": "rejected",
            "dead_letter": "dead_letter",
        },
    )
    workflow.add_conditional_edges(
        "verification",
        route_after_verification,
        {
            "policy": "policy",
            "human_review": "human_review",
            "rejected": "rejected",
        },
    )
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
    workflow.add_edge("human_review", "verification")
    workflow.add_edge("output", END)

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
    graph = get_compiled_graph()
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
    graph = get_compiled_graph()
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
    graph = get_compiled_graph()
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
