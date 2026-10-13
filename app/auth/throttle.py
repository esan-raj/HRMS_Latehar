import hashlib
from datetime import timedelta, timezone

from flask import request
from sqlalchemy import select

from app.audit import utcnow
from app.extensions import db
from app.models import LoginThrottle

WINDOW = timedelta(minutes=15)
LIMIT = 8


def too_many_attempts(email: str) -> bool:
    now = utcnow()
    for bucket in _buckets(email):
        row = db.session.scalar(select(LoginThrottle).where(LoginThrottle.bucket == bucket))
        if row is None:
            continue
        started = row.window_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if now - started < WINDOW and row.attempts >= LIMIT:
            return True
    return False


def record_failure(email: str) -> None:
    now = utcnow()
    for bucket in _buckets(email):
        row = db.session.scalar(select(LoginThrottle).where(LoginThrottle.bucket == bucket))
        if row is None:
            db.session.add(LoginThrottle(bucket=bucket, attempts=1, window_started_at=now, updated_at=now))
            continue
        started = row.window_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if now - started >= WINDOW:
            row.attempts = 1
            row.window_started_at = now
        else:
            row.attempts += 1
        row.updated_at = now
    db.session.commit()


def clear_attempts(email: str) -> None:
    buckets = _buckets(email)
    rows = db.session.scalars(select(LoginThrottle).where(LoginThrottle.bucket.in_(buckets))).all()
    for row in rows:
        db.session.delete(row)
    db.session.commit()


def _buckets(email: str) -> list[str]:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return [f"ip:{request.remote_addr or 'unknown'}", f"email:{digest}"]
