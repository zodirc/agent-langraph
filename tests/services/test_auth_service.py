from app.config.settings import Settings
from app.services.auth_service import AuthService


def test_jwt_roundtrip(tmp_path, monkeypatch):
    config = tmp_path / "cfg.yaml"
    config.write_text(
        """
app:
  secret_key: test-secret-key
auth:
  enabled: true
  jwt_expire_minutes: 60
  users:
    - username: tester
      password: pass123
      user_id: u-1
      role: admin
  api_keys:
    - key: service-key-abc
      user_id: svc
      role: admin
""",
        encoding="utf-8",
    )
    import app.config.settings as settings_module
    import app.services.auth_service as auth_module

    settings = Settings(str(config))
    monkeypatch.setattr(settings_module, "settings", settings)
    monkeypatch.setattr(auth_module, "settings", settings)
    auth = AuthService()

    principal = auth.authenticate_user("tester", "pass123")
    assert principal is not None
    token = auth.create_access_token(principal)
    decoded = auth.decode_access_token(token)
    assert decoded is not None
    assert decoded.user_id == "u-1"
    assert decoded.role == "admin"

    api_principal = auth.authenticate_api_key("service-key-abc")
    assert api_principal is not None
    assert api_principal.user_id == "svc"
