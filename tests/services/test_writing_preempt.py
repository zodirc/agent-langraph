from app.services.artifact_content import SteerPreempted, generate_artifact_content
from app.services.mission_steer import normalize_pending_entries, queue_steer_message
from app.services.state_store import get_state_store


def test_forced_pause_steer_escalates_priority(base_state):
    state = {**base_state, "status": "MISSION_RUNNING"}
    get_state_store().save(state)

    updated = queue_steer_message(
        state["task_id"],
        intervention={"action": "pause", "force": True},
    )
    entries = normalize_pending_entries(updated.get("pending_user_message"))
    assert entries
    last = entries[-1]
    assert int(last.get("priority") or 0) >= 100
    assert bool(last.get("preempt")) is True


def test_generate_artifact_content_raises_on_forced_pause(base_state):
    state = {**base_state, "status": "MISSION_RUNNING"}
    get_state_store().save(state)
    queue_steer_message(
        state["task_id"],
        intervention={"action": "pause", "force": True},
    )

    try:
        generate_artifact_content(
            state=state,
            tool_name="append_text_artifact",
            filename="novel.txt",
            goal="继续写",
            target_chars=200,
        )
        assert False, "expected SteerPreempted"
    except SteerPreempted:
        assert True
