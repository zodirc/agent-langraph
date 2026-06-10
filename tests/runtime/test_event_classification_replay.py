"""Replay-style cases for core event types (WP-1.1)."""

from __future__ import annotations

import pytest

from app.runtime.state import create_initial_state, merge_state
from app.runtime.event_router import route_after_event_classification
from app.services.event_classification import classify_user_event


REPLAY_CASES = [
    {
        "name": "new_task_default",
        "payload": {"goal": "Explain agent runtime", "risk_level": "LOW"},
        "state": {},
        "expected_event_type": "new_task",
    },
    {
        "name": "interrupt_preempt_stop",
        "payload": {"goal": "停止"},
        "state": {
            "session_turn": 2,
            "status": "RUNNING",
            "input_payload": {"fsm_state": "RUNNING"},
        },
        "active_run": True,
        "expected_event_type": "interrupt",
    },
    {
        "name": "resume_checkpoint",
        "payload": {"goal": "继续", "resume_checkpoint_ref": "step-3"},
        "state": {"session_turn": 4},
        "expected_event_type": "resume",
    },
    {
        "name": "status_query_progress",
        "payload": {"goal": "当前进度？"},
        "state": {"session_turn": 2},
        "active_run": True,
        "expected_event_type": "status_query",
    },
]


@pytest.mark.parametrize("case", REPLAY_CASES, ids=[c["name"] for c in REPLAY_CASES])
def test_event_classification_replay_cases(case):
    from app.services.graph_run_registry import begin_graph_run, end_graph_run

    state = create_initial_state(
        task_id=f"replay-{case['name']}",
        input_payload=dict(case["payload"]),
    )
    if case.get("state"):
        state = merge_state(state, **case["state"])
    run_id = begin_graph_run(state["task_id"]) if case.get("active_run") else None
    try:
        result = classify_user_event(state, payload=case["payload"])
    finally:
        if run_id:
            end_graph_run(state["task_id"], run_id)
    assert result.event_type == case["expected_event_type"]


def test_event_classification_routes_to_acknowledge(base_state):
    state = merge_state(base_state, event_type="interrupt")
    assert route_after_event_classification(state) == "acknowledge"
    from app.runtime.graph import build_agent_graph

    workflow = build_agent_graph()
    graph = workflow.compile().get_graph()
    start_edges = [edge for edge in graph.edges if edge.source == "__start__"]
    assert start_edges[0].target == "event_classification"
    ack_edges = [
        edge for edge in graph.edges if edge.source == "event_classification"
    ]
    assert any(edge.target == "acknowledge" for edge in ack_edges)
    plan_edges = [edge for edge in graph.edges if edge.source == "acknowledge"]
    assert any(edge.target == "interrupt_control" for edge in plan_edges)


def test_graph_stream_visits_acknowledge_before_incremental_planning(isolated_stores, test_settings, monkeypatch):
    """Integration: ACK node runs after classification and before incremental_planning."""
    from app.nodes import incremental_planning_node as planning_module
    from app.runtime.graph import stream_graph
    from app.runtime.state import TaskStatus, create_initial_state, merge_state

    def abort_after_planning(state):
        return merge_state(
            state,
            status=TaskStatus.REJECTED.value,
            current_node="incremental_planning",
            policy_result="STOP",
        )

    monkeypatch.setattr(planning_module, "incremental_planning_node", abort_after_planning)

    state = create_initial_state(
        input_payload={"goal": "Explain agent runtime", "risk_level": "LOW"},
    )
    visited: list[str] = []
    for node_name, _snapshot in stream_graph(state):
        visited.append(node_name)
        if node_name == "incremental_planning":
            break

    assert visited[:5] == [
        "event_classification",
        "acknowledge",
        "interrupt_control",
        "incremental_planning",
    ]
