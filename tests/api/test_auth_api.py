from fastapi.testclient import TestClient

from app.main import app


def test_login_when_auth_disabled():
    client = TestClient(app)
    resp = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 400


def test_login_with_auth_enabled(tmp_path, monkeypatch):
    config = tmp_path / "cfg.yaml"
    config.write_text(
        """
app:
  secret_key: secret
  env: test
storage:
  sqlite_path: {db}
  vectorstore_path: {vs}
  log_path: {log}
auth:
  enabled: true
  users:
    - username: admin
      password: admin
      user_id: admin
      role: admin
""".format(
            db=str(tmp_path / "a.db"),
            vs=str(tmp_path / "vs"),
            log=str(tmp_path / "l.log"),
        ),
        encoding="utf-8",
    )
    from app.config.settings import Settings
    import app.api.auth_api as auth_api_module
    import app.config.settings as settings_module

    s = Settings(str(config))
    monkeypatch.setattr(settings_module, "settings", s)
    monkeypatch.setattr(auth_api_module, "settings", s)

    client = TestClient(app)
    bad = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401

    ok = client.post("/auth/login", json={"username": "admin", "password": "admin"})
    assert ok.status_code == 200
    assert "access_token" in ok.json()
