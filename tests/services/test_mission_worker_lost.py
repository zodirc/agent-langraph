from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.graph_run_registry import clear_all_graph_runs_for_tests
from app.services.mission_execution import PAUSE_WORKER_LOST
from app.services.mission_worker_lost import (
    reconcile_worker_lost,
    should_reconcile_worker_lost,
)


def setup_function() -> None:
    clear_all_graph_runs_for_tests()


def test_should_reconcile_orphan_running():
    state = merge_state(
        create_initial_state(),
        status=TaskStatus.MISSION_RUNNING.value,
        mission={"kind": "writing", "phase": "executing"},
        execution_run={"run_id": "dead-run", "started_at": "t"},
    )
    assert should_reconcile_worker_lost(state)


def test_should_not_reconcile_when_executor_active():
    from app.services.graph_run_registry import begin_graph_run

    state = create_initial_state()
    run_id = begin_graph_run(state["task_id"])
    state = merge_state(
        state,
        status=TaskStatus.MISSION_RUNNING.value,
        mission={"kind": "writing"},
        execution_run={"run_id": run_id, "started_at": "t"},
    )
    assert not should_reconcile_worker_lost(state)


def test_reconcile_pauses_with_worker_lost(isolated_stores):
    state = merge_state(
        create_initial_state(),
        status=TaskStatus.MISSION_RUNNING.value,
        mission={"kind": "writing"},
        current_node="mission_decide",
    )
    updated = reconcile_worker_lost(state, persist=True)
    assert updated["status"] == TaskStatus.MISSION_PAUSED.value
    assert updated["mission_control"]["pause_reason"] == PAUSE_WORKER_LOST

    from app.services.state_store import get_state_store

    loaded = get_state_store().load(state["task_id"])
    assert loaded is not None
    assert loaded["status"] == TaskStatus.MISSION_PAUSED.value


def test_graph_runner_finalize_turn_reconciles_orphan():
    from app.services.graph_runner import GraphRunner

    state = merge_state(
        create_initial_state(),
        status=TaskStatus.MISSION_RUNNING.value,
        mission={"kind": "writing", "phase": "executing"},
        current_node="mission_decide",
    )
    finalized = GraphRunner()._finalize_turn(state)
    assert finalized["status"] == TaskStatus.MISSION_PAUSED.value
    assert finalized["mission_control"]["pause_reason"] == PAUSE_WORKER_LOST
