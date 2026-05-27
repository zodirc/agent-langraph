import threading

from app.services.graph_execution_pool import GraphExecutionRejected, reset_graph_execution_pool
from app.services.graph_runner import GraphRunner


def test_start_task_rejects_when_pool_saturated(
    isolated_stores, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "GRAPH_RUNNER_MAX_CONCURRENT", 1)
    monkeypatch.setattr(test_settings, "GRAPH_RUNNER_QUEUE_TIMEOUT_SEC", 0.15)
    monkeypatch.setattr(test_settings, "GRAPH_RUNNER_BACKPRESSURE_ENABLED", True)
    reset_graph_execution_pool()
    import app.services.graph_runner as runner_mod

    runner_mod._runner = None

    hold_slot = threading.Event()
    slot_taken = threading.Event()

    def slow_run(self, state, thread, mode):
        slot_taken.set()
        hold_slot.wait(timeout=2)
        return {
            **state,
            "status": "COMPLETED",
            "final_answer": "ok",
            "audit_log": state.get("audit_log") or [],
        }

    monkeypatch.setattr(
        "app.services.graph_runner.GraphRunner._invoke_graph_safe",
        slow_run,
    )

    runner = GraphRunner()
    errors: list[Exception] = []

    def run_task():
        try:
            runner.start_task(
                user_id="u",
                input_payload={"goal": "a", "risk_level": "LOW", "needs_search": False},
            )
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=run_task)
    t1.start()
    assert slot_taken.wait(timeout=2), "first task did not acquire pool slot"

    t2 = threading.Thread(target=run_task)
    t2.start()
    t2.join(timeout=2)
    hold_slot.set()
    t1.join(timeout=3)

    assert any(isinstance(e, GraphExecutionRejected) for e in errors)
    reset_graph_execution_pool()
    runner_mod._runner = None
