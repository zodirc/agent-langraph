"""Intent observation wall-clock timeout fallback."""

import time
from unittest.mock import patch

from app.runtime.state import create_initial_state
from app.services.intent_observation import _invoke_observation_model
from app.services.pre_planning import seed_pre_planning_route_audit


def test_invoke_observation_model_timeout_falls_back_to_structural(monkeypatch):
    monkeypatch.setattr(
        "app.services.intent_observation.settings.MODEL_TIMEOUT_INTENT_OBSERVATION",
        1,
    )

    def slow_invoke(*_args, **_kwargs):
        time.sleep(2)
        return {"intent_kind": "qa", "target_mode": "qa_mode", "confidence": 0.9}

    state = create_initial_state(
        task_id="io-timeout",
        input_payload={"goal": "解释一下这个复杂问题", "interaction_mode": "auto"},
    )
    seed = seed_pre_planning_route_audit(state)

    with patch("app.services.llm_client.invoke_structured", side_effect=slow_invoke):
        result = _invoke_observation_model(state, route_audit_seed=seed, explicit_mode="auto")

    assert result.fallback_used is True
    assert result.source in ("hybrid", "structural")
    assert "llm_timeout" in " ".join(result.reasons)
