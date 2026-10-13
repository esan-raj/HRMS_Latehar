import uuid

from sqlalchemy import select

from app.extensions import db


def active_choices(model, *, selected=None, label=None, blank=None):
    label = label or (lambda row: row.name)
    rows = list(db.session.scalars(select(model).where(model.is_active.is_(True)).order_by(model.name)))
    selected_id = _uuid(selected)
    if selected_id is not None and all(row.id != selected_id for row in rows):
        current = db.session.get(model, selected_id)
        if current is not None:
            rows.append(current)
    choices = [_choice(row, label) for row in rows]
    if blank is not None:
        choices.insert(0, ("", blank))
    return choices


def all_choices(model, *, label=None, blank=None):
    label = label or (lambda row: row.name)
    rows = list(db.session.scalars(select(model).order_by(model.name)))
    choices = [_choice(row, label) for row in rows]
    if blank is not None:
        choices.insert(0, ("", blank))
    return choices


def facility_label(row) -> str:
    return f"{row.name} ({row.code})"


def _choice(row, label) -> tuple[str, str]:
    text = label(row)
    if not row.is_active:
        text = f"{text} (inactive)"
    return str(row.id), text


def _uuid(value):
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
