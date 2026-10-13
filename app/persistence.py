import uuid

from flask import abort
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import changes_between, column_snapshot, record_audit, utcnow
from app.domain import FieldError, StaleEditError
from app.extensions import db


def reload_locked(model, object_id):
    statement = select(model).where(model.id == object_id).execution_options(populate_existing=True)
    if db.engine.dialect.name == "postgresql":
        statement = statement.with_for_update()
    obj = db.session.execute(statement).scalar_one_or_none()
    if obj is None:
        abort(404)
    return obj


def commit_or_conflict(default_field: str = "name") -> None:
    try:
        db.session.commit()
    except IntegrityError as exc:
        detail = str(getattr(exc, "orig", exc)).lower()
        db.session.rollback()
        if "employee_code" in detail:
            raise FieldError("employee_code", "An employee with this code already exists.") from None
        if "facilities.code" in detail or "uq_facilities_code" in detail:
            raise FieldError("code", "A facility with this code already exists.") from None
        if "name_ci" in detail:
            raise FieldError("name", "A record with this name already exists.") from None
        if "uq_postings_one_open" in detail or "one_open" in detail:
            raise FieldError(
                "end_date",
                "This employee already has an open posting. End that posting before opening another.",
            ) from None
        if "foreign key" in detail:
            raise FieldError(
                default_field,
                "This record is still used by other records. Deactivate it instead of deleting it.",
            ) from None
        raise FieldError(default_field, "This record conflicts with an existing one.") from None


def apply_versioned_update(record, submitted_version: int, values: dict, fields: tuple[str, ...]):
    if record.version != submitted_version:
        db.session.rollback()
        raise StaleEditError()
    before = column_snapshot(record, fields)
    for key, value in values.items():
        setattr(record, key, value)
    after = column_snapshot(record, fields)
    if not changes_between(before, after):
        return before, after, False
    record.version = submitted_version + 1
    record.updated_at = utcnow()
    return before, after, True


def new_id() -> uuid.UUID:
    return uuid.uuid4()
