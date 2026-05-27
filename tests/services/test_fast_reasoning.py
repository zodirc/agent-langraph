from app.services.fast_reasoning import try_fast_reasoning


def test_fast_reasoning_skips_runtime_info(test_settings, monkeypatch):
    import app.services.fast_reasoning as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    state = {
        "input_payload": {"goal": "你的token限制是多少，单次回答"},
        "selected_tools": ["get_runtime_info"],
        "tool_results": [
            {
                "tool": "get_runtime_info",
                "status": "ok",
                "result": {
                    "model_name": "test-model",
                    "model_max_tokens_reasoning": 8192,
                    "web_search": False,
                    "available_tools": ["echo"],
                },
            }
        ],
    }
    assert try_fast_reasoning(state) is None


def test_fast_reasoning_calculator(test_settings, monkeypatch):
    import app.services.fast_reasoning as mod

    monkeypatch.setattr(mod, "settings", test_settings)
    state = {
        "selected_tools": ["calculator"],
        "tool_results": [
            {
                "tool": "calculator",
                "status": "ok",
                "result": {"expression": "1+2", "result": "3", "status": "ok"},
            }
        ],
    }
    out = try_fast_reasoning(state)
    assert out is not None
    assert "3" in out["summary"]
