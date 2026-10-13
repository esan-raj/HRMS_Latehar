from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth.jwt_tokens import AuthError, verify_access_token
from app.auth.supabase_api import SupabaseAuthError
from tests.conftest import OTHER_ID, authenticate, make_token


def test_unauthenticated_pages_redirect_to_login(client):
    response = client.get("/employees")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert client.get("/").status_code == 302
    assert client.get("/masters/departments").status_code == 302


def test_other_admin_token_is_forbidden(client, app):
    authenticate(client, app, OTHER_ID)
    response = client.get("/employees")
    assert response.status_code == 403
    assert b"do not have access" in response.data


def test_expired_and_invalid_tokens_are_rejected(client, app):
    authenticate(client, app, lifetime=-30)
    expired = client.get("/employees")
    assert expired.status_code == 302
    assert "/login" in expired.headers["Location"]

    authenticate(client, app, secret="a-different-secret-that-is-long-enough")
    invalid = client.get("/employees")
    assert invalid.status_code == 302
    assert "/login" in invalid.headers["Location"]


def test_health_is_public_and_has_no_secrets(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    body = response.get_data(as_text=True).lower()
    assert "password" not in body
    assert "postgres" not in body
    assert "secret" not in body


def test_logout_clears_cookies_and_revokes(client, app, monkeypatch):
    seen = {}

    def revoke(access_token, refresh_token):
        seen["access"] = access_token
        seen["refresh"] = refresh_token

    monkeypatch.setattr("app.auth.routes.revoke_session", revoke)
    authenticate(client, app)
    client.set_cookie("hrms_refresh", "refresh-value")
    response = client.post("/logout")
    assert response.status_code == 302
    assert seen["refresh"] == "refresh-value"
    cleared = " ".join(response.headers.getlist("Set-Cookie")).lower()
    assert "hrms_access" in cleared
    assert "hrms_refresh" in cleared


def test_login_hides_password_failure_and_non_admin(client, app, monkeypatch):
    def reject(email, password):
        assert password
        raise SupabaseAuthError(status=400)

    monkeypatch.setattr("app.auth.routes.password_grant", reject)
    response = client.post("/login", data={"email": "admin@example.com", "password": "not-the-password"})
    assert response.status_code == 401
    assert b"Invalid email or password." in response.data
    assert b"not-the-password" not in response.data

    other = make_token(app, OTHER_ID)

    def accept(email, password):
        return {"access_token": other, "refresh_token": "refresh-value", "expires_in": 3600}

    revoked = {}
    monkeypatch.setattr("app.auth.routes.password_grant", accept)
    monkeypatch.setattr("app.auth.routes.revoke_session", lambda access, refresh: revoked.update(access=access))
    response = client.post("/login", data={"email": "other@example.com", "password": "correct-password"})
    assert response.status_code == 401
    assert b"Invalid email or password." in response.data
    assert "hrms_access=" not in " ".join(response.headers.getlist("Set-Cookie"))
    assert revoked["access"] == other


def test_login_is_throttled(client, monkeypatch):
    calls = {"count": 0}

    def reject(email, password):
        calls["count"] += 1
        raise SupabaseAuthError(status=401)

    monkeypatch.setattr("app.auth.routes.password_grant", reject)
    for _ in range(8):
        response = client.post("/login", data={"email": "admin@example.com", "password": "wrong-password"})
        assert response.status_code == 401
    blocked = client.post("/login", data={"email": "admin@example.com", "password": "wrong-password"})
    assert blocked.status_code == 429
    assert b"Too many login attempts" in blocked.data
    assert calls["count"] == 8


def test_local_hmac_does_not_trust_a_bad_signature(app, monkeypatch):
    called = []
    monkeypatch.setattr(
        "app.auth.jwt_tokens.fetch_auth_user",
        lambda token: called.append(token) or {"id": app.config["ADMIN_USER_ID"], "aud": "authenticated"},
    )
    with app.app_context():
        with pytest.raises(AuthError):
            verify_access_token(make_token(app, app.config["ADMIN_USER_ID"], secret="wrong-secret-value-32-characters"))
    assert called == []


def test_asymmetric_jwt_uses_jwks_and_checks_signature(app, monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    other_key = ec.generate_private_key(ec.SECP256R1())

    class Key:
        def __init__(self, value):
            self.key = value

    class Client:
        def __init__(self, key):
            self.key = key

        def get_signing_key_from_jwt(self, token):
            return Key(self.key)

    monkeypatch.setattr("app.auth.jwt_tokens.PyJWKClient", lambda *args, **kwargs: Client(private_key.public_key()))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": app.config["ADMIN_USER_ID"],
        "aud": "authenticated",
        "iss": app.config["SUPABASE_JWT_ISSUER"],
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": "test-key"})
    with app.app_context():
        claims = verify_access_token(token)
        assert claims["sub"] == app.config["ADMIN_USER_ID"]
        monkeypatch.setattr("app.auth.jwt_tokens.PyJWKClient", lambda *args, **kwargs: Client(other_key.public_key()))
        app.extensions.pop("hrms_jwks", None)
        with pytest.raises(AuthError):
            verify_access_token(token)


def test_legacy_hs256_uses_auth_server_not_unverified_payload(app, monkeypatch):
    app.config["SUPABASE_JWT_SECRET"] = None
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": app.config["ADMIN_USER_ID"],
            "aud": "authenticated",
            "iss": "https://attacker.example/auth/v1",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        "unrelated-secret-with-enough-length",
        algorithm="HS256",
    )
    monkeypatch.setattr(
        "app.auth.jwt_tokens.fetch_auth_user",
        lambda access: {"id": OTHER_ID, "aud": "authenticated"},
    )
    with app.app_context():
        claims = verify_access_token(token)
        assert claims["sub"] == OTHER_ID
        monkeypatch.setattr(
            "app.auth.jwt_tokens.fetch_auth_user",
            lambda access: {"id": app.config["ADMIN_USER_ID"], "aud": "someone-else"},
        )
        with pytest.raises(AuthError):
            verify_access_token(token)


def test_none_algorithm_is_rejected(app):
    token = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiIxMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTEifQ."
    with app.app_context():
        with pytest.raises(AuthError):
            verify_access_token(token)
