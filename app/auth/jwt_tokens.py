import uuid

import jwt
from flask import current_app
from jwt import PyJWKClient

from app.auth.supabase_api import SupabaseAuthError, fetch_auth_user


class AuthError(Exception):
    """The access token is not a verified administrator session."""


def verify_access_token(token: str | None) -> dict:
    """Return verified claims.

    The JWT header is read only to choose a key. Claims are returned only after
    signature, expiry, issuer, audience, and subject checks succeed.
    Asymmetric tokens (current Supabase projects) are checked locally with the
    project JWKS. Legacy HS256 tokens are checked with the Auth server unless
    the server is explicitly configured with the legacy shared secret.
    """
    if not token or token.count(".") != 2:
        raise AuthError
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise AuthError from exc
    algorithm = header.get("alg")
    if algorithm in {"ES256", "RS256"}:
        return _verify_asymmetric(token, algorithm)
    if algorithm == "HS256":
        secret = current_app.config.get("SUPABASE_JWT_SECRET")
        if secret:
            return _decode(token, secret, ["HS256"])
        return _verify_with_auth_server(token)
    raise AuthError


def _verify_asymmetric(token: str, algorithm: str) -> dict:
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
    except Exception as exc:
        raise AuthError from exc
    return _decode(token, signing_key, [algorithm])


def _decode(token: str, key, algorithms: list[str]) -> dict:
    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=current_app.config["SUPABASE_JWT_AUDIENCE"],
            issuer=current_app.config["SUPABASE_JWT_ISSUER"],
            leeway=5,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.InvalidTokenError as exc:
        raise AuthError from exc


def _verify_with_auth_server(token: str) -> dict:
    try:
        user = fetch_auth_user(token)
    except SupabaseAuthError as exc:
        raise AuthError from exc
    if not isinstance(user, dict):
        raise AuthError
    try:
        subject = str(uuid.UUID(str(user.get("id"))))
    except (TypeError, ValueError) as exc:
        raise AuthError from exc
    audience = user.get("aud")
    expected_audience = current_app.config["SUPABASE_JWT_AUDIENCE"]
    if audience != expected_audience:
        raise AuthError
    return {
        "sub": subject,
        "aud": audience,
        "iss": current_app.config["SUPABASE_JWT_ISSUER"],
    }


def _jwks_client() -> PyJWKClient:
    url = current_app.config["SUPABASE_JWKS_URL"]
    cached = current_app.extensions.get("hrms_jwks")
    if cached and cached["url"] == url:
        return cached["client"]
    headers = {"Accept": "application/json"}
    api_key = current_app.config.get("SUPABASE_PUBLISHABLE_KEY")
    if api_key:
        headers["apikey"] = api_key
    client = PyJWKClient(url, cache_keys=True, max_cached_keys=8, lifespan=600, headers=headers, timeout=10)
    current_app.extensions["hrms_jwks"] = {"url": url, "client": client}
    return client
