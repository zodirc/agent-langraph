from app.services.checkpoint_recovery import (
    detect_corrupt_checkpoint,
    reset_thread,
)


def test_valid_state_not_corrupt():
    state = {
        "task_id": "t1",
        "status": "NEW",
        "session_id": "s1",
        "node_history": [{"node": "planning"}],
    }
    result = detect_corrupt_checkpoint("t1:t1", state=state)
    assert result["corrupt"] is False


def test_missing_required_fields_corrupt():
    result = detect_corrupt_checkpoint("t1", state={"session_id": "s1"})
    assert result["corrupt"] is True
    assert "task_id" in result["reason"]


def test_node_loop_detected():
    history = [{"node": "tool_execution"}] * 15
    state = {
        "task_id": "t1",
        "status": "RUNNING",
        "session_id": "s1",
        "node_history": history,
    }
    result = detect_corrupt_checkpoint("t1", state=state, max_node_repeat=10)
    assert result["corrupt"] is True
    assert "loop" in result["reason"]


def test_reset_thread_calls_checkpointer(monkeypatch):
    deleted: list[str] = []

    class FakeCp:
        def delete_thread(self, thread_id: str) -> None:
            deleted.append(thread_id)

    monkeypatch.setattr(
        "app.runtime.checkpointer.create_checkpointer",
        lambda: FakeCp(),
    )
    assert reset_thread("task-1:t2", reason="test") is True
    assert deleted == ["task-1:t2"]
