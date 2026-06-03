from app.runtime.state import create_initial_state
from app.services.graph_run_registry import (
    begin_graph_run,
    clear_all_graph_runs_for_tests,
    end_graph_run,
    executor_active_for_state,
    is_graph_run_active,
)


def setup_function() -> None:
    clear_all_graph_runs_for_tests()


def test_graph_run_register_and_end():
    run_id = begin_graph_run("task-a")
    assert is_graph_run_active("task-a", run_id)
    end_graph_run("task-a", run_id)
    assert not is_graph_run_active("task-a", run_id)


def test_executor_active_for_state():
    state = create_initial_state(task_id="task-b")
    assert not executor_active_for_state(state)
    run_id = begin_graph_run("task-b")
    state["execution_run"] = {"run_id": run_id, "started_at": "t"}
    assert executor_active_for_state(state)
    end_graph_run("task-b", run_id)
    assert not executor_active_for_state(state)
