from app.services.planning_retry_signals import apply_force_write_signal


def test_force_edit_signal_for_character_rename():
    state = {
        "task_id": "t1",
        "session_id": "s1",
        "input_payload": {
            "goal": "主角梁致远→梁志远，秦池→秦梅",
            "writing_operator": "character",
            "writing_intent": {"enabled": True},
        },
    }
    updated = apply_force_write_signal(state)
    payload = updated["input_payload"]
    assert payload.get("force_edit_after_reads") is True
    assert "force_write_after_reads" not in payload
    assert "edit_artifact" in payload["route_audit_replan_feedback"]
