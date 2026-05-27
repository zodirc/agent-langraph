from __future__ import annotations

from typing import Any, Iterator, Optional, cast

from langgraph.graph import END, StateGraph

from app.nodes.dead_letter_node import dead_letter_node
from app.nodes.human_review_node import human_review_node
from app.nodes.memory_writeback_node import memory_writeback_node
from app.nodes.mission_act_node import mission_act_node
from app.nodes.mission_decide_node import mission_decide_node
from app.nodes.mission_eval_node import mission_eval_node
from app.nodes.mission_finalize_node import mission_finalize_node
from app.nodes.mission_init_node import mission_init_node
from app.nodes.mission_observe_node import mission_observe_node
from app.nodes.output_guard_node import output_guard_node
from app.nodes.output_node import output_node
from app.nodes.policy_node import policy_node
from app.nodes.rejected_node import rejected_node
from app.runtime.checkpointer import create_checkpointer
from app.runtime.langgraphics_wrap import resolve_compiled_graph
from app.runtime.graph_cache import cached_graph_compiler
from app.runtime.mission_router import (
    route_after_mission_decide,
    route_after_mission_eval,
)
from app.runtime.router import (
    route_after_output_guard,
    route_after_policy_to_guard,
)
from app.runtime.state import AgentState, append_node_history, ensure_agent_state, merge_state
from app.services.session_turn import graph_thread_id


def build_mission_graph() -> StateGraph:
    """
    Mission runtime: ReAct control loop (decide → act → observe → eval) then deliver.
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("mission_init", mission_init_node)
    workflow.add_node("mission_decide", mission_decide_node)
    workflow.add_node("mission_act", mission_act_node)
    workflow.add_node("mission_observe", mission_observe_node)
    workflow.add_node("mission_eval", mission_eval_node)
    workflow.add_node("mission_finalize", mission_finalize_node)
    workflow.add_node("policy", policy_node)
    workflow.add_node("output_guard", output_guard_node)
    workflow.add_node("human_review", human_review_node)
    workflow.add_node("rejected", rejected_node)
    workflow.add_node("dead_letter", dead_letter_node)
    workflow.add_node("output", output_node)
    workflow.add_node("memory_writeback", memory_writeback_node)

    workflow.set_entry_point("mission_init")
    workflow.add_edge("mission_init", "mission_decide")

    workflow.add_conditional_edges(
        "mission_decide",
        route_after_mission_decide,
        {
            "mission_act": "mission_act",
            "finalize": "mission_finalize",
            "policy": "mission_finalize",
        },
    )

    workflow.add_edge("mission_act", "mission_observe")
    workflow.add_edge("mission_observe", "mission_eval")

    workflow.add_conditional_edges(
        "mission_eval",
        route_after_mission_eval,
        {
            "mission_decide": "mission_decide",
            "finalize": "mission_finalize",
            "dead_letter": "dead_letter",
        },
    )

    workflow.add_edge("mission_finalize", "policy")
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


def _track(state: AgentState, node_name: str) -> AgentState:
    return append_node_history(state, node_name)


def _compile_mission(workflow: Any) -> Any:
    return workflow.compile(
        checkpointer=create_checkpointer(),
        interrupt_before=["human_review"],
    )


get_compiled_mission_graph = cached_graph_compiler(build_mission_graph, compile_fn=_compile_mission)


def run_mission_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> AgentState:
    graph = resolve_compiled_graph(get_compiled_mission_graph, "mission")
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    result = graph.invoke(state, config)
    if isinstance(result, dict):
        return ensure_agent_state(result)
    return cast(AgentState, result)


def stream_mission_graph(
    state: AgentState,
    *,
    thread_id: Optional[str] = None,
) -> Iterator[tuple[str, AgentState]]:
    graph = resolve_compiled_graph(get_compiled_mission_graph, "mission")
    config = {"configurable": {"thread_id": thread_id or graph_thread_id(state)}}
    latest: AgentState = state
    for chunk in graph.stream(state, config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node_name, update in chunk.items():
            if isinstance(update, dict):
                # Use merge_state to preserve nested dicts like progress.work_plan.
                latest = merge_state(latest, **update)
            latest = _track(latest, node_name)
            yield node_name, latest
