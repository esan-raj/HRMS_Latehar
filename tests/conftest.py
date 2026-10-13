import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy.pool import StaticPool

from app import create_app
from app.extensions import db

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"


class TestingConfig:
    TESTING = True
    SECRET_KEY = "test-secret-key-not-used-in-production"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    WTF_CSRF_ENABLED = False
    SUPABASE_URL = "https://example.supabase.co"
    SUPABASE_PUBLISHABLE_KEY = "test-publishable-key"
    SUPABASE_JWT_SECRET = "test-jwt-secret-with-enough-length-32b"
    SUPABASE_JWT_AUDIENCE = "authenticated"
    ADMIN_USER_ID = ADMIN_ID
    COOKIE_SECURE = False
    TRUST_PROXY = False


class CsrfConfig(TestingConfig):
    WTF_CSRF_ENABLED = True


@pytest.fixture
def app():
    application = create_app(TestingConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_token(app, subject, **overrides):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "aud": overrides.get("audience", app.config["SUPABASE_JWT_AUDIENCE"]),
        "iss": overrides.get("issuer", app.config["SUPABASE_JWT_ISSUER"]),
        "iat": now,
        "exp": now + timedelta(seconds=overrides.get("lifetime", 3600)),
        "role": "authenticated",
    }
    secret = overrides.get("secret", app.config["SUPABASE_JWT_SECRET"])
    headers = overrides.get("headers")
    return jwt.encode(payload, secret, algorithm=overrides.get("algorithm", "HS256"), headers=headers)


def authenticate(client, app, subject=None, **overrides):
    token = make_token(app, subject or app.config["ADMIN_USER_ID"], **overrides)
    client.set_cookie("hrms_access", token)
    return token


@pytest.fixture
def admin(client, app):
    authenticate(client, app)
    return client
