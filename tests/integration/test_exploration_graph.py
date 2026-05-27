"""Exploration graph pilot — analysis domain."""

from app.runtime.exploration_graph import run_exploration_graph
from app.runtime.state import create_initial_state, merge_state


def test_exploration_graph_completes(base_state, isolated_stores):
    state = merge_state(
        create_initial_state(),
        input_payload={
            "goal": "探索三种方案的可行性",
            "domain": "analysis",
            "execution_mode": "exploration",
        },
        execution_mode="exploration",
    )
    final = run_exploration_graph(state)
    assert final.get("exploration")
    assert final.get("reasoning_result", {}).get("summary")
    assert final.get("reasoning_result", {}).get("structured", {}).get("exploration") is True
    assert final["status"] in ("COMPLETED", "WAITING_REVIEW", "REJECTED")
