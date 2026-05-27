from app.nodes.dead_letter_node import dead_letter_node
from app.runtime.state import TaskStatus, merge_state
from app.services.dead_letter_store import get_dead_letter_store


def test_dead_letter_node_enqueues(base_state, isolated_stores, test_settings):
    dlq = get_dead_letter_store()
    state = merge_state(
        base_state,
        errors=["tool_execution: boom"],
        retry_count=3,
        status=TaskStatus.TOOL_FAILED.value,
    )
    result = dead_letter_node(state)
    assert result["status"] == TaskStatus.DEAD_LETTER.value
    entry = dlq.get(state["task_id"])
    assert entry is not None
    assert entry["retry_count"] == 3
