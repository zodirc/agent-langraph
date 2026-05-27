from app.services.runtime_capabilities import (
    build_runtime_capabilities,
    reasoning_instructions_for_state,
)


def test_build_runtime_capabilities_lists_tools_and_paths():
    caps = build_runtime_capabilities()
    assert "execution_paths" in caps
    assert any(p["id"] == "mission" for p in caps["execution_paths"])
    assert caps["registered_tools"]
    assert "get_runtime_info" in {t["name"] for t in caps["registered_tools"]}


def test_reasoning_instructions_mention_writing_when_intent_enabled():
    state = {
        "input_payload": {"writing_intent": {"enabled": True, "action": "write_outline"}},
    }
    text = reasoning_instructions_for_state(state)
    assert "Writing" in text or "writing" in text.lower()
    assert "never" in text.lower() or "NEVER" in text
