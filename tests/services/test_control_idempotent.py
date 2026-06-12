"""Control API idempotency for missing tasks (方案 C)."""

from app.services.graph_runner import get_graph_runner
from app.services.task_tombstone import clear_all_tombstones_for_tests, is_task_tombstoned


def test_pause_cancel_already_gone(isolated_stores):
    runner = get_graph_runner()
    missing = "ghost-task-idempotent"
    pause = runner.pause_task(missing)
    assert pause.get("outcome") == "already_gone"
    assert pause.get("accepted") is True
    cancel = runner.cancel_task(missing)
    assert cancel.get("outcome") == "already_gone"


def test_delete_missing_task_purges_and_tombstones(isolated_stores):
    from app.services.task_cleanup import purge_task_remains
    from app.services.task_tombstone import mark_task_tombstone

    clear_all_tombstones_for_tests()
    missing = "ghost-delete-idempotent"
    mark_task_tombstone(missing)
    result = purge_task_remains(missing)
    assert isinstance(result, dict)
    assert is_task_tombstoned(missing)
