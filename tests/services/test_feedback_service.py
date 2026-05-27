from app.runtime.state import create_initial_state
from app.services.feedback_service import record_feedback
from app.services.memory_store import get_memory_store
from app.services.state_store import get_state_store


def test_record_feedback_writes_memory(base_state, isolated_stores):
    state = create_initial_state(task_id=base_state["task_id"], user_id=base_state["user_id"])
    state = dict(state)
    state["reasoning_result"] = {"summary": "test answer", "confidence": 0.8}
    get_state_store().save(state)

    result = record_feedback(
        task_id=state["task_id"],
        user_id=state["user_id"],
        rating=5,
        comment="helpful",
        outcome="success",
    )
    assert result["rating"] == 5
    assert "feedback_positive" in result["tags"]
    hits = get_memory_store().search(
        "helpful",
        limit=5,
        user_id=state["user_id"],
    )
    assert any("feedback" in (h.get("tags") or []) for h in hits)
