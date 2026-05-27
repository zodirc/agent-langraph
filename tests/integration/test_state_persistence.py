from app.services.graph_runner import GraphRunner
from app.services.state_store import get_state_store


def test_state_persistence_after_run(isolated_stores):
    runner = GraphRunner()
    state = runner.start_task(input_payload={"goal": "persist me", "risk_level": "LOW"})
    loaded = get_state_store().load(state["task_id"])
    assert loaded is not None
    assert loaded["task_id"] == state["task_id"]
