import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, g, make_response, render_template
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from werkzeug.middleware.proxy_fix import ProxyFix

from app.auth.cookies import clear_auth_cookies
from app.auth.guards import register_guards
from app.config import config_from_env
from app.database import register_search_path
from app.extensions import csrf, db, migrate


def create_app(config_class=None):
    app = Flask(__name__)
    app.config.from_object(config_class or config_from_env())
    _normalize_config(app)
    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    from app import models  # noqa: F401

    migrate.init_app(app, db, directory=str(Path(__file__).resolve().parent.parent / "migrations"))
    csrf.init_app(app)
    register_search_path()
    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def _sqlite_foreign_keys(dbapi_connection, _connection_record):
            if db.engine.dialect.name != "sqlite":
                return
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    from app.auth.routes import bp as auth_bp
    from app.dashboard import bp as dashboard_bp
    from app.employee_routes import bp as employees_bp
    from app.field_routes import bp as fields_bp
    from app.health import bp as health_bp
    from app.master_routes import bp as masters_bp
    from app import record_routes  # noqa: F401
    from app.seed import register_commands

    app.url_map.strict_slashes = False
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(employees_bp)
    app.register_blueprint(fields_bp)
    app.register_blueprint(masters_bp)
    app.register_blueprint(health_bp)
    for rule in app.url_map.iter_rules():
        rule.strict_slashes = False
    register_commands(app)
    register_guards(app)
    _register_errors(app)

    @app.context_processor
    def _field_rules():
        from app.fielddefs import field_input_rules

        return {"field_input_rules": field_input_rules}

    @app.template_filter("show_date")
    def show_date(value):
        if not value:
            return "—"
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).strftime("%d %b %Y")
        return value.strftime("%d %b %Y")

    return app


def _normalize_config(app) -> None:
    app.config["ADMIN_USER_ID"] = str(uuid.UUID(str(app.config["ADMIN_USER_ID"])))
    supabase_url = str(app.config["SUPABASE_URL"]).rstrip("/")
    app.config["SUPABASE_URL"] = supabase_url
    app.config.setdefault("SUPABASE_JWKS_URL", f"{supabase_url}/auth/v1/.well-known/jwks.json")
    app.config.setdefault("SUPABASE_JWT_ISSUER", f"{supabase_url}/auth/v1")
    app.config.setdefault("SUPABASE_JWT_AUDIENCE", "authenticated")
    if not app.config.get("SUPABASE_JWT_SECRET"):
        app.config["SUPABASE_JWT_SECRET"] = None
    secure = bool(app.config.get("COOKIE_SECURE"))
    app.config["SESSION_COOKIE_SECURE"] = secure
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("SESSION_COOKIE_NAME", "hrms_session")
    app.config["WTF_CSRF_SSL_STRICT"] = secure
    app.config.setdefault("MAX_CONTENT_LENGTH", 1_000_000)


def _register_errors(app) -> None:
    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        app.logger.info("CSRF validation failed.")
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def forbidden(_error):
        g.new_auth_cookies = None
        response = make_response(render_template("errors/403.html"), 403)
        clear_auth_cookies(response)
        return response

    @app.errorhandler(404)
    def missing(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(_error):
        app.logger.error("Unhandled application error (%s).", type(_error).__name__)
        return render_template("errors/500.html"), 500
