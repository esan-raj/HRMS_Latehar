import json
import urllib.error
import urllib.request

from flask import current_app


class SupabaseAuthError(Exception):
    def __init__(self, status: int | None = None, temporary: bool = False):
        super().__init__("supabase auth request failed")
        self.status = status
        self.temporary = temporary


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def password_grant(email: str, password: str) -> dict:
    payload = _request(
        "POST",
        "/auth/v1/token?grant_type=password",
        {"email": email, "password": password},
    )
    return _tokens(payload)


def refresh_grant(refresh_token: str) -> dict:
    payload = _request(
        "POST",
        "/auth/v1/token?grant_type=refresh_token",
        {"refresh_token": refresh_token},
    )
    return _tokens(payload)


def fetch_auth_user(access_token: str) -> dict:
    payload = _request("GET", "/auth/v1/user", bearer=access_token)
    if not isinstance(payload, dict):
        raise SupabaseAuthError(temporary=True)
    return payload


def revoke_session(access_token: str | None, refresh_token: str | None) -> None:
    token = access_token
    if not token and refresh_token:
        try:
            token = refresh_grant(refresh_token)["access_token"]
        except SupabaseAuthError:
            token = None
    if not token:
        return
    try:
        _request("POST", "/auth/v1/logout?scope=local", {}, bearer=token)
    except SupabaseAuthError:
        current_app.logger.info("Session revocation did not succeed.")


def _tokens(payload: dict) -> dict:
    access = payload.get("access_token") if isinstance(payload, dict) else None
    refresh = payload.get("refresh_token") if isinstance(payload, dict) else None
    expires_in = payload.get("expires_in", 3600) if isinstance(payload, dict) else 3600
    if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh:
        raise SupabaseAuthError(temporary=True)
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError):
        expires_in = 3600
    if expires_in < 60:
        expires_in = 60
    return {"access_token": access, "refresh_token": refresh, "expires_in": expires_in}


def _request(method: str, path: str, body: dict | None = None, bearer: str | None = None):
    headers = {
        "apikey": current_app.config["SUPABASE_PUBLISHABLE_KEY"],
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    url = current_app.config["SUPABASE_URL"].rstrip("/") + path
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=15) as response:
            raw = response.read()
            if not raw:
                return {}
            parsed = json.loads(raw.decode())
            if not isinstance(parsed, dict):
                raise SupabaseAuthError(temporary=True)
            return parsed
    except urllib.error.HTTPError as exc:
        exc.read()
        raise SupabaseAuthError(exc.code, temporary=exc.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SupabaseAuthError(temporary=True) from exc
