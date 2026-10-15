import uuid

from sqlalchemy import String, cast, literal, null, select, union_all

from app.extensions import db
from app.models import Designation, EmploymentType, Facility


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


def register_filter_choices():
    """Load the three register filter lists in one query."""
    designations = select(
        literal("designation").label("kind"),
        Designation.id.label("id"),
        Designation.name.label("name"),
        cast(null(), String).label("code"),
        Designation.is_active.label("is_active"),
    )
    facilities = select(
        literal("facility").label("kind"),
        Facility.id.label("id"),
        Facility.name.label("name"),
        Facility.code.label("code"),
        Facility.is_active.label("is_active"),
    )
    employment_types = select(
        literal("employment_type").label("kind"),
        EmploymentType.id.label("id"),
        EmploymentType.name.label("name"),
        cast(null(), String).label("code"),
        EmploymentType.is_active.label("is_active"),
    )
    combined = union_all(designations, facilities, employment_types).subquery()
    rows = db.session.execute(select(combined).order_by(combined.c.kind, combined.c.name)).all()
    grouped = {
        "designation": [("", "All designations")],
        "facility": [("", "All facilities")],
        "employment_type": [("", "All employment types")],
    }
    for kind, row_id, name, code, is_active in rows:
        text = facility_label_parts(name, code) if kind == "facility" else name
        if not is_active:
            text = f"{text} (inactive)"
        grouped[kind].append((str(row_id), text))
    return grouped["designation"], grouped["facility"], grouped["employment_type"]


def facility_label_parts(name: str, code: str) -> str:
    return f"{name} ({code})"


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
