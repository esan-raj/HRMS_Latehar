import sys
import uuid
from urllib.parse import urlsplit

from dotenv import load_dotenv
import os


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be zero or greater.")
    return value


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgresql+psycopg://"):
        parts = urlsplit(url)
        if parts.port == 6543:
            raise RuntimeError(
                "DATABASE_URL uses port 6543, which is Supabase transaction pooling. "
                "Use a direct connection or the session pooler on port 5432."
            )
        host = (parts.hostname or "").lower()
        if "sslmode=" not in url and host not in {"localhost", "127.0.0.1"}:
            join = "&" if parts.query else "?"
            url = f"{url}{join}sslmode=require"
    return url


def config_from_env():
    load_dotenv()
    missing = [
        name
        for name in ("FLASK_SECRET_KEY", "DATABASE_URL", "SUPABASE_URL", "ADMIN_USER_ID")
        if not (os.getenv(name) or "").strip()
    ]
    publishable = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not publishable:
        missing.append("SUPABASE_PUBLISHABLE_KEY")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    secret = os.environ["FLASK_SECRET_KEY"].strip()
    if len(secret) < 32:
        raise RuntimeError("FLASK_SECRET_KEY must be at least 32 characters.")

    supabase_url = os.environ["SUPABASE_URL"].strip().rstrip("/")
    if not supabase_url.startswith("https://"):
        raise RuntimeError("SUPABASE_URL must be the https project URL.")

    try:
        admin_user_id = str(uuid.UUID(os.environ["ADMIN_USER_ID"].strip()))
    except ValueError as exc:
        raise RuntimeError("ADMIN_USER_ID must be the administrator's Supabase Auth user UUID.") from exc

    database_url = os.environ["DATABASE_URL"].strip()
    if "db" in sys.argv and (os.getenv("MIGRATION_DATABASE_URL") or "").strip():
        database_url = os.environ["MIGRATION_DATABASE_URL"].strip()
    database_url = normalize_database_url(database_url)

    cookie_secure = _flag("COOKIE_SECURE")
    jwt_secret = (os.getenv("SUPABASE_JWT_SECRET") or "").strip() or None

    class Config:
        SECRET_KEY = secret
        SQLALCHEMY_DATABASE_URI = database_url
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_size": _int("DB_POOL_SIZE", 5),
            "max_overflow": _int("DB_MAX_OVERFLOW", 2),
            "pool_timeout": 30,
            "pool_recycle": 240,
            "pool_pre_ping": True,
            "connect_args": {"options": "-csearch_path=hrms,public"},
        }
        SUPABASE_URL = supabase_url
        SUPABASE_PUBLISHABLE_KEY = publishable
        SUPABASE_JWT_SECRET = jwt_secret
        SUPABASE_JWT_AUDIENCE = (os.getenv("SUPABASE_JWT_AUDIENCE") or "authenticated").strip()
        SUPABASE_JWT_ISSUER = (os.getenv("SUPABASE_JWT_ISSUER") or f"{supabase_url}/auth/v1").strip()
        ADMIN_USER_ID = admin_user_id
        COOKIE_SECURE = cookie_secure
        TRUST_PROXY = _flag("TRUST_PROXY")
        SESSION_COOKIE_NAME = "hrms_session"
        SESSION_COOKIE_HTTPONLY = True
        SESSION_COOKIE_SAMESITE = "Lax"
        SESSION_COOKIE_SECURE = cookie_secure
        WTF_CSRF_SSL_STRICT = cookie_secure
        MAX_CONTENT_LENGTH = 1_000_000

    return Config
