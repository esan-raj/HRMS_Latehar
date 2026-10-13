from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth.cookies import ACCESS_COOKIE, REFRESH_COOKIE, clear_auth_cookies, set_auth_cookies
from app.auth.guards import safe_next_url
from app.auth.jwt_tokens import AuthError, verify_access_token
from app.auth.supabase_api import SupabaseAuthError, password_grant, revoke_session
from app.auth.throttle import clear_attempts, record_failure, too_many_attempts
from app.extensions import db
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, HiddenField
from wtforms.validators import DataRequired, Email, Length

bp = Blueprint("auth", __name__)

INVALID_LOGIN = "Invalid email or password."
THROTTLED_LOGIN = "Too many login attempts. Try again later."
UNAVAILABLE_LOGIN = "Sign-in is temporarily unavailable. Try again later."


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        filters=[lambda value: (value or "").strip().lower()],
        validators=[DataRequired(), Email(), Length(max=254)],
        render_kw={"autocomplete": "username", "inputmode": "email"},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(max=200)],
        render_kw={"autocomplete": "current-password"},
    )
    next = HiddenField()


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and _current_admin():
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if request.method == "GET":
        form.next.data = safe_next_url(request.args.get("next")) or ""
    if form.validate_on_submit():
        email = form.email.data
        if too_many_attempts(email):
            flash(THROTTLED_LOGIN, "danger")
            return render_template("auth/login.html", form=form), 429
        try:
            tokens = password_grant(email, form.password.data)
            claims = verify_access_token(tokens["access_token"])
        except SupabaseAuthError as exc:
            record_failure(email)
            current_app_log("Login rejected.")
            flash(UNAVAILABLE_LOGIN if exc.temporary else INVALID_LOGIN, "danger")
            status = 503 if exc.temporary else 401
            return render_template("auth/login.html", form=form), status
        except AuthError:
            record_failure(email)
            current_app_log("Login rejected.")
            flash(UNAVAILABLE_LOGIN, "danger")
            return render_template("auth/login.html", form=form), 503
        subject = str(claims.get("sub", "")).lower()
        if subject != current_admin_id():
            revoke_session(tokens["access_token"], tokens["refresh_token"])
            record_failure(email)
            current_app_log("Login rejected.")
            flash(INVALID_LOGIN, "danger")
            return render_template("auth/login.html", form=form), 401
        clear_attempts(email)
        destination = safe_next_url(form.next.data) or url_for("dashboard.index")
        response = redirect(destination)
        set_auth_cookies(response, tokens["access_token"], tokens["refresh_token"], tokens["expires_in"])
        return response
    status = 400 if request.method == "POST" else 200
    return render_template("auth/login.html", form=form), status


@bp.route("/logout", methods=["POST"])
def logout():
    revoke_session(request.cookies.get(ACCESS_COOKIE), request.cookies.get(REFRESH_COOKIE))
    response = redirect(url_for("auth.login"))
    clear_auth_cookies(response)
    db.session.rollback()
    return response


def _current_admin() -> bool:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        return False
    try:
        claims = verify_access_token(token)
    except AuthError:
        return False
    return str(claims.get("sub", "")).lower() == current_admin_id()


def current_admin_id() -> str:
    from flask import current_app

    return current_app.config["ADMIN_USER_ID"].lower()


def current_app_log(message: str) -> None:
    from flask import current_app

    current_app.logger.info(message)
