from flask import current_app

ACCESS_COOKIE = "hrms_access"
REFRESH_COOKIE = "hrms_refresh"
REFRESH_MAX_AGE = 60 * 60 * 24 * 14


def set_auth_cookies(response, access_token: str, refresh_token: str, expires_in: int):
    options = _options()
    response.set_cookie(ACCESS_COOKIE, access_token, max_age=int(expires_in), **options)
    response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=REFRESH_MAX_AGE, **options)
    return response


def clear_auth_cookies(response):
    options = _options()
    response.delete_cookie(ACCESS_COOKIE, path="/", samesite=options["samesite"], secure=options["secure"])
    response.delete_cookie(REFRESH_COOKIE, path="/", samesite=options["samesite"], secure=options["secure"])
    return response


def _options():
    return {
        "httponly": True,
        "secure": bool(current_app.config.get("COOKIE_SECURE")),
        "samesite": "Lax",
        "path": "/",
    }
