from app.config.prompts import build_reasoning_system_prompt, resolve_reasoning_mode
from app.runtime.state import create_initial_state, merge_state


def test_resolve_reasoning_mode_from_payload():
    state = merge_state(
        create_initial_state(),
        input_payload={"reasoning_mode": "cot"},
    )
    assert resolve_reasoning_mode(state) == "cot"


def test_build_cot_prompt():
    prompt = build_reasoning_system_prompt("cot")
    assert "chain-of-thought" in prompt.lower()


def test_build_react_prompt():
    prompt = build_reasoning_system_prompt("react")
    assert "react" in prompt.lower()
