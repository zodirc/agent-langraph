"""Terminal task statuses must not be overwritten by in-flight graph snapshots."""

from app.runtime.state import TaskStatus, create_initial_state, merge_state
from app.services.state_store import StateStore


def test_save_preserves_timed_out_from_planned_snapshot(tmp_path):
    db = tmp_path / "terminal.db"
    store = StateStore(str(db))
    task_id = "terminal-guard"
    timed_out = merge_state(
        create_initial_state(task_id=task_id),
        status=TaskStatus.TIMED_OUT.value,
        current_node="watchdog",
        final_answer="任务已超时",
    )
    store.save(timed_out)

    stale = merge_state(
        create_initial_state(task_id=task_id),
        status=TaskStatus.PLANNED.value,
        current_node="planning",
    )
    store.save(stale)

    loaded = store.load(task_id)
    assert loaded is not None
    assert loaded.get("status") == TaskStatus.TIMED_OUT.value
    assert loaded.get("current_node") == "watchdog"
    assert "超时" in str(loaded.get("final_answer") or "")
