from __future__ import annotations

import os

from app.config.settings import Settings


def test_settings_resolves_deepseek_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("MODEL_NAME", "deepseek-v4-flash")
    monkeypatch.delenv("MODEL_BASE_URL", raising=False)

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        """
model:
  provider: anthropic
  name: claude-sonnet-4-5
  base_url: ""
  api_key: ""
  enabled: true
app:
  env: test
  secret_key: test
storage:
  sqlite_path: {db}
""".format(db=tmp_path / "a.db"),
        encoding="utf-8",
    )
    s = Settings(str(cfg))
    assert s.MODEL_PROVIDER == "deepseek"
    assert s.MODEL_API_KEY == "sk-test"
    assert s.MODEL_NAME == "deepseek-v4-flash"
    assert s.MODEL_BASE_URL == "https://api.deepseek.com"
    assert s.MODEL_ENABLED is True
