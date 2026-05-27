from app.services.manuscript_service import build_writing_intent, resolve_manuscript


def test_build_writing_intent_defers_when_mission_block_present():
    ms = resolve_manuscript("t-mission", None)
    intent = build_writing_intent(
        goal="write many chapters",
        selected_tools=["write_text_artifact"],
        manuscript=ms,
        session_turn=1,
        llm_intent={"enabled": True, "action": "write_body"},
        mission_block={
            "kind": "writing",
            "total_target_chars": 500000,
            "step_policy": {"chars_per_step": 4000},
        },
    )
    assert intent.get("enabled") is False
    assert intent.get("delegated_to") == "mission"
