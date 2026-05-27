import time

from app.domain.worker_executor import run_workers_parallel


def test_run_workers_parallel(isolated_stores, monkeypatch):
    from app.domain import worker_executor

    def slow_worker(**kwargs):
        time.sleep(0.05)
        return {
            "subtask_id": kwargs.get("subtask_id", "x"),
            "domain": kwargs["domain"],
            "status": "COMPLETED",
            "summary": kwargs["domain"],
        }

    calls = {"n": 0}

    def counting_worker(**kwargs):
        calls["n"] += 1
        return slow_worker(**kwargs)

    monkeypatch.setattr(worker_executor, "execute_domain_worker", counting_worker)

    subtasks = [
        {"subtask_id": "a", "domain": "document", "description": "d1", "status": "PENDING"},
        {"subtask_id": "b", "domain": "code", "description": "d2", "status": "PENDING"},
        {"subtask_id": "c", "domain": "analysis", "description": "d3", "status": "PENDING"},
    ]
    start = time.time()
    results, updated, errors, _ = run_workers_parallel(
        parent_task_id="parent-1",
        user_id="tester",
        subtasks=subtasks,
        context={},
        max_workers=3,
    )
    elapsed = time.time() - start

    assert len(results) == 3
    assert calls["n"] == 3
    assert not errors
    assert elapsed < 0.25
