import jwt

from app.config.settings import settings
from app.services.auth_service import AuthPrincipal, get_auth_service, hash_password


def test_jwt_includes_tenant_claim(monkeypatch):
    monkeypatch.setattr(settings, "APP_SECRET_KEY", "test-secret")
    monkeypatch.setattr(settings, "JWT_EXPIRE_MINUTES", 60)
    principal = AuthPrincipal(
        user_id="u1",
        role="user",
        auth_method="jwt",
        username="alice",
        tenant_id="acme",
        tenant_ids=["acme"],
    )
    token = get_auth_service().create_access_token(principal)
    payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
    assert payload["tenant_id"] == "acme"
    decoded = get_auth_service().decode_access_token(token)
    assert decoded is not None
    assert decoded.tenant_id == "acme"


def test_password_hash_verification(monkeypatch):
    hashed = hash_password("secret")
    monkeypatch.setattr(
        settings,
        "AUTH_USERS",
        [
            {
                "username": "bob",
                "password_hash": hashed,
                "user_id": "bob",
                "role": "user",
                "tenant_id": "t1",
            }
        ],
    )
    principal = get_auth_service().authenticate_user("bob", "secret")
    assert principal is not None
    assert principal.tenant_id == "t1"
    assert get_auth_service().authenticate_user("bob", "wrong") is None
