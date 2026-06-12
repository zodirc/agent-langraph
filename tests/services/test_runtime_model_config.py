from __future__ import annotations

from app.services.runtime_model_config import get_runtime_model_config


def test_runtime_model_apply_and_reset(monkeypatch):
    store = get_runtime_model_config()
    store.reset()

    eff = store.apply(
        provider="openai",
        model_name="gpt-4o-mini",
        api_key="sk-runtime-test",
        base_url="https://api.example.com/v1",
        enabled=True,
    )
    assert eff.provider == "openai"
    assert eff.model_name == "gpt-4o-mini"
    assert eff.api_key == "sk-runtime-test"
    assert eff.base_url == "https://api.example.com/v1"
    assert eff.enabled is True
    assert eff.source == "runtime"

    api = store.for_api()
    assert api["api_key_hint"] == "***test"
    assert api["source"] == "runtime"

    store.apply(model_name="gpt-4o")
    eff2 = store.effective()
    assert eff2.model_name == "gpt-4o"
    assert eff2.api_key == "sk-runtime-test"

    store.reset()
    eff3 = store.effective()
    assert eff3.source == "env"
