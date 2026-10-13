import uuid
from datetime import date, datetime, timezone

SENSITIVE_FIELDS = {
    "password",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "authorization",
    "apikey",
    "api_key",
    "jwt",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def column_snapshot(obj, names) -> dict:
    return {name: json_safe(getattr(obj, name)) for name in names}


def changes_between(before: dict, after: dict) -> dict:
    changes = {}
    for key in sorted(set(before) | set(after)):
        if key.lower() in SENSITIVE_FIELDS:
            continue
        if before.get(key) != after.get(key):
            changes[key] = {"before": before.get(key), "after": after.get(key)}
    return changes


def record_audit(actor_id, entity_type: str, entity_id, action: str, before: dict, after: dict) -> None:
    from app.extensions import db
    from app.models import AuditEvent

    changes = changes_between(before or {}, after or {})
    if action == "update" and not changes:
        return
    if isinstance(actor_id, str):
        actor_id = uuid.UUID(actor_id)
    if isinstance(entity_id, str):
        entity_id = uuid.UUID(entity_id)
    db.session.add(
        AuditEvent(
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            changes=changes,
            created_at=utcnow(),
        )
    )
