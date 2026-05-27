from app.nodes.supervisor_decompose_node import supervisor_decompose_node
from app.nodes.supervisor_merge_node import supervisor_merge_node
from app.nodes.supervisor_worker_node import supervisor_worker_node
from app.runtime.state import TaskStatus, merge_state


def test_supervisor_pipeline(base_state, isolated_stores, monkeypatch):
    from app.domain import worker_executor

    def fake_parallel(**kwargs):
        subtasks = kwargs["subtasks"]
        results = {}
        for st in subtasks:
            results[st["subtask_id"]] = {
                "subtask_id": st["subtask_id"],
                "domain": st["domain"],
                "status": "COMPLETED",
                "summary": f"done {st['domain']}",
                "confidence": 0.9,
                "risk_level": "LOW",
            }
        updated = [{**st, "status": "COMPLETED"} for st in subtasks]
        return results, updated, [], []

    monkeypatch.setattr(worker_executor, "run_workers_parallel", fake_parallel)

    state = merge_state(
        base_state,
        input_payload={"goal": "analyze document and code quality", "domains": ["document", "code"]},
    )
    decomposed = supervisor_decompose_node(state)
    assert decomposed["status"] == TaskStatus.PLANNED.value
    assert len(decomposed.get("subtasks") or []) >= 1

    worked = supervisor_worker_node(decomposed)
    assert worked.get("worker_results")
    assert worked["status"] == TaskStatus.REASONED.value

    merged = supervisor_merge_node(worked)
    assert merged["reasoning_result"] is not None
    assert merged["reasoning_result"]["structured"]["supervisor"] is True
