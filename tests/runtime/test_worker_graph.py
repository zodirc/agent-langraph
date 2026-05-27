from app.runtime.state import create_initial_state, merge_state
from app.runtime.worker_graph import run_worker_graph


def test_worker_graph_completes(isolated_stores):
    state = create_initial_state(
        task_id="worker-graph-1",
        input_payload={"goal": "summarize architecture", "needs_search": True},
    )
    state = merge_state(
        state,
        plan=["retrieve knowledge"],
        selected_tools=[],
        status="PLANNED",
    )
    result = run_worker_graph(state)
    assert result.get("node_history")
    assert len(result["node_history"]) >= 1
