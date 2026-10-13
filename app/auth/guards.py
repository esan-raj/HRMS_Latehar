import uuid

from flask import current_app, g, redirect, request, url_for

from app.auth.cookies import ACCESS_COOKIE, REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.auth.jwt_tokens import AuthError, verify_access_token
from app.auth.supabase_api import SupabaseAuthError, refresh_grant

PUBLIC_ENDPOINTS = {"health.health", "auth.login", "auth.logout", "static"}


def register_guards(app) -> None:
    @app.before_request
    def require_admin():
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        claims = _claims_from_cookies()
        if claims is None:
            return _login_redirect()
        subject = str(claims.get("sub", "")).lower()
        if subject != app.config["ADMIN_USER_ID"].lower():
            g.new_auth_cookies = None
            from flask import abort

            abort(403)
        g.actor_id = uuid.UUID(subject)
        return None

    @app.after_request
    def secure_response(response):
        refreshed = getattr(g, "new_auth_cookies", None)
        if refreshed:
            set_auth_cookies(
                response,
                refreshed["access_token"],
                refreshed["refresh_token"],
                refreshed.get("expires_in", 3600),
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; style-src 'self' https://cdn.jsdelivr.net; "
            "script-src 'self'; form-action 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response


def _claims_from_cookies():
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        try:
            return verify_access_token(token)
        except AuthError:
            current_app.logger.info("Access token rejected.")
    if getattr(g, "refresh_attempted", False):
        return None
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        return None
    g.refresh_attempted = True
    try:
        payload = refresh_grant(refresh_token)
        claims = verify_access_token(payload["access_token"])
    except (AuthError, SupabaseAuthError):
        current_app.logger.info("Refresh token rejected.")
        return None
    g.new_auth_cookies = payload
    return claims


def _login_redirect():
    target = url_for("auth.login")
    if request.method == "GET":
        nxt = request.full_path
        if nxt.endswith("?") and not request.query_string:
            nxt = request.path
        if _safe_next(nxt) and nxt != "/login":
            target = url_for("auth.login", next=nxt)
    response = redirect(target)
    clear_auth_cookies(response)
    return response


def safe_next_url(value: str | None) -> str | None:
    if not value or len(value) > 500:
        return None
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    return value


def _safe_next(value: str | None) -> bool:
    return safe_next_url(value) is not None
