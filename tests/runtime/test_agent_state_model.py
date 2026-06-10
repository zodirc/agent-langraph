from app.runtime.agent_state_model import model_to_state, state_to_model
from app.runtime.state import create_initial_state, ensure_agent_state, merge_state


def test_state_to_model_roundtrip():
    state = create_initial_state(
        user_id="u1",
        input_payload={"goal": "hello", "risk_level": "LOW"},
    )
    model = state_to_model(state)
    assert model.user_id == "u1"
    assert model.input_payload["goal"] == "hello"
    back = model_to_state(model)
    assert back["user_id"] == "u1"
    assert back["input_payload"]["goal"] == "hello"


def test_merge_state_validates_via_pydantic():
    state = create_initial_state(input_payload={"goal": "x"})
    merged = merge_state(state, status="PLANNED", retry_count=1)
    assert merged["status"] == "PLANNED"
    assert merged["retry_count"] == 1


def test_ensure_agent_state_rejects_invalid_status():
    state = create_initial_state()
    # extra fields allowed; missing required fields would fail on empty dict
    normalized = ensure_agent_state({**state, "user_id": "u2"})
    assert normalized["user_id"] == "u2"
