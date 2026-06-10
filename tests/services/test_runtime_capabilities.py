from app.services.runtime_capabilities import (
    build_runtime_capabilities,
    reasoning_instructions_for_state,
)


def test_build_runtime_capabilities_lists_actions_and_tools():
    caps = build_runtime_capabilities()
    assert "action_set" in caps
    assert "write_artifact" in caps["action_set"]
    assert "run_tool" in caps["action_set"]
    assert caps["registered_tools"]
    assert "get_runtime_info" in {t["name"] for t in caps["registered_tools"]}


def test_reasoning_instructions_mention_writing_when_intent_enabled():
    state = {
        "input_payload": {"writing_intent": {"enabled": True, "action": "write_outline"}},
    }
    text = reasoning_instructions_for_state(state)
    assert "artifact" in text.lower()
    assert "never" in text.lower()
