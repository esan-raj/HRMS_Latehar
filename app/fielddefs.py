import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import g, has_request_context
from sqlalchemy import String, and_, bindparam, case, cast, event, false, func, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, selectinload

from app.audit import column_snapshot, record_audit
from app.domain import FieldError
from app.extensions import db
from app.models import Employee, EmployeeField, EmployeeFieldOption, FieldCatalog, Posting
from app.persistence import commit_or_conflict, new_id, reload_locked

CUSTOM_TYPES = (
    ("short_text", "Short text"),
    ("long_text", "Long text"),
    ("integer", "Integer"),
    ("decimal", "Decimal"),
    ("date", "Date"),
    ("dropdown", "Dropdown"),
    ("yes_no", "Yes/no"),
)
CUSTOM_TYPE_KEYS = {key for key, _label in CUSTOM_TYPES}
SORTABLE_TYPES = {"short_text", "integer", "decimal", "date", "yes_no"}
FILTERABLE_TYPES = SORTABLE_TYPES | {"dropdown"}
SYSTEM_FIELDS = (
    {
        "field_key": "full_name",
        "label": "Employee name",
        "help_text": None,
        "placeholder": None,
        "field_type": "short_text",
        "section": "Personal Details",
        "sort_order": 10,
        "show_in_register": True,
    },
    {
        "field_key": "facility_id",
        "label": "Facility",
        "help_text": "Choose the employee's facility. Add a missing facility from Facilities first.",
        "placeholder": None,
        "field_type": "facility",
        "section": "Personal Details",
        "sort_order": 20,
        "show_in_register": True,
    },
    {
        "field_key": "phone",
        "label": "Mobile number",
        "help_text": "Exactly 10 digits, starting with 6, 7, 8, or 9. Do not include a country code.",
        "placeholder": "9867123450",
        "field_type": "short_text",
        "section": "Personal Details",
        "sort_order": 30,
        "show_in_register": True,
    },
)
RESERVED_KEYS = {
    "full_name",
    "facility_id",
    "phone",
    "employee_code",
    "email",
    "password",
    "custom_data",
    "id",
    "version",
    "token",
    "secret",
}
FIELD_SNAPSHOT = (
    "field_key",
    "label",
    "help_text",
    "placeholder",
    "field_type",
    "required",
    "section",
    "sort_order",
    "show_in_register",
    "is_archived",
)
KEY_RE = re.compile(r"^cf_[0-9a-f]{32}$")
SHORT_LIMIT = 200
_AADHAAR_LABEL = re.compile(r"\baadhaa?r\b", re.IGNORECASE)
LONG_LIMIT = 4000
_PREVIOUS_PHONE_HELP = {
    "7 to 20 characters: digits, spaces, and + ( ) . - . Stored as text, so leading zeros are kept.",
    "10 to 15 digits. Spaces and + ( ) . - are allowed. Stored as text, so leading zeros are kept.",
}


def session_or_default(session: Session | None) -> Session:
    return session if session is not None else db.session


def clear_request_field_cache() -> None:
    """Drop field metadata cached for the current request only."""
    if not has_request_context():
        return
    g.pop("_hrms_fields", None)
    g.pop("_hrms_catalog", None)
    g.pop("_hrms_incomplete_ids", None)
    g.pop("_hrms_incomplete_count", None)


def _cacheable(session: Session) -> bool:
    return has_request_context() and session is db.session


def _query_fields(session: Session) -> list[EmployeeField]:
    return list(
        session.scalars(select(EmployeeField).order_by(EmployeeField.sort_order, EmployeeField.label))
    )


def load_dropdown_options(fields: list[EmployeeField]) -> None:
    """Load dropdown choices for these fields in one query."""
    pending = [
        field
        for field in fields
        if field.field_type == "dropdown" and "options" in sa_inspect(field).unloaded
    ]
    if not pending:
        return
    db.session.execute(
        select(EmployeeField)
        .where(EmployeeField.id.in_([field.id for field in pending]))
        .options(selectinload(EmployeeField.options))
        .execution_options(populate_existing=True)
    )


def _remember_fields(session: Session, catalog: FieldCatalog, fields: list[EmployeeField]) -> None:
    if not _cacheable(session):
        return
    g._hrms_catalog = catalog
    g._hrms_fields = sorted(fields, key=lambda field: (field.sort_order, field.label))


@event.listens_for(Session, "after_commit")
def _drop_field_cache_after_commit(_session) -> None:
    clear_request_field_cache()


@event.listens_for(Session, "after_rollback")
def _drop_field_cache_after_rollback(_session) -> None:
    clear_request_field_cache()


def ensure_system_fields(session: Session | None = None, commit: bool = True) -> FieldCatalog:
    session = session_or_default(session)
    if _cacheable(session) and getattr(g, "_hrms_fields", None) is not None and getattr(g, "_hrms_catalog", None) is not None:
        return g._hrms_catalog
    catalog = session.get(FieldCatalog, 1)
    created = catalog is None
    if catalog is None:
        catalog = FieldCatalog(id=1, version=1)
        session.add(catalog)
    fields = _query_fields(session)
    by_key = {field.field_key: field for field in fields}
    changed = False
    for spec in SYSTEM_FIELDS:
        existing = by_key.get(spec["field_key"])
        if existing is not None:
            if spec["field_key"] == "phone" and existing.help_text in _PREVIOUS_PHONE_HELP:
                existing.help_text = spec["help_text"]
                changed = True
            continue
        created = True
        row = EmployeeField(
            id=new_id(),
            version=1,
            required=True,
            is_system=True,
            is_archived=False,
            **spec,
        )
        session.add(row)
        fields.append(row)
        by_key[row.field_key] = row
    if created or changed:
        session.flush()
        if commit:
            session.commit()
            return catalog
    _remember_fields(session, catalog, fields)
    return catalog


def is_aadhaar_field(field) -> bool:
    return bool(_AADHAAR_LABEL.search(getattr(field, "label", "") or ""))


def aadhaar_digits(value) -> str:
    return re.sub(r"\D", "", "" if value is None else str(value))


def aadhaar_problem(value) -> str | None:
    """Return an error unless the value is 12 digits, with optional spaces."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if not re.fullmatch(r"[0-9 ]+", text):
        return "Enter only numbers."
    if len(text.replace(" ", "")) != 12:
        return "Enter a 12-digit Aadhaar number."
    return None


def field_input_rules(field: EmployeeField) -> dict:
    """HTML constraints that match the server checks for one custom field."""
    attrs = {}
    hint = ""
    if is_aadhaar_field(field):
        if field.required:
            attrs["required"] = "required"
            attrs["aria-required"] = "true"
        attrs["inputmode"] = "numeric"
        attrs["maxlength"] = "20"
        attrs["data-aadhaar"] = "1"
        attrs["autocomplete"] = "off"
        attrs["title"] = "Enter a 12-digit number. Spaces are allowed."
        hint = "12 digits. Spaces are allowed."
        placeholder = "1234 5678 9012"
        return {"attrs": attrs, "hint": hint, "placeholder": placeholder}
    if field.required:
        attrs["required"] = "required"
        attrs["aria-required"] = "true"
    if field.field_type == "short_text":
        attrs["maxlength"] = str(SHORT_LIMIT)
        hint = f"Up to {SHORT_LIMIT} characters."
    elif field.field_type == "long_text":
        attrs["maxlength"] = str(LONG_LIMIT)
        hint = f"Up to {LONG_LIMIT} characters."
    elif field.field_type == "integer":
        attrs["inputmode"] = "numeric"
        attrs["pattern"] = r"[+-]?[0-9]+"
        attrs["title"] = "Enter a whole number between -1000000000 and 1000000000."
        hint = attrs["title"]
    elif field.field_type == "decimal":
        attrs["inputmode"] = "decimal"
        attrs["pattern"] = r"[+-]?[0-9]{1,12}(\.[0-9]{1,4})?"
        attrs["title"] = "Enter a decimal with up to 12 digits and 4 decimal places."
        hint = attrs["title"]
    elif field.field_type == "date":
        attrs["min"] = "1900-01-01"
        attrs["max"] = "2100-12-31"
        attrs["title"] = "Enter a date between 1900 and 2100."
        hint = attrs["title"]
    elif field.field_type in {"yes_no", "dropdown"}:
        hint = "Choose a value from the list."
    return {"attrs": attrs, "hint": hint}


def catalog_version(session: Session | None = None) -> int:
    session = session_or_default(session)
    if _cacheable(session) and getattr(g, "_hrms_catalog", None) is not None:
        return g._hrms_catalog.version
    return ensure_system_fields(session, commit=session is db.session).version


def ordered_fields(session: Session | None = None, include_archived: bool = True) -> list[EmployeeField]:
    session = session_or_default(session)
    if _cacheable(session):
        fields = getattr(g, "_hrms_fields", None)
        if fields is None:
            ensure_system_fields(session)
            fields = getattr(g, "_hrms_fields", None)
        if fields is None:
            fields = _query_fields(session)
            catalog = session.get(FieldCatalog, 1)
            if catalog is not None:
                _remember_fields(session, catalog, fields)
            else:
                fields = sorted(fields, key=lambda field: (field.sort_order, field.label))
        rows = fields if include_archived else [field for field in fields if not field.is_archived]
        return sorted(rows, key=lambda field: (field.sort_order, field.label))
    statement = select(EmployeeField).order_by(EmployeeField.sort_order, EmployeeField.label)
    if not include_archived:
        statement = statement.where(EmployeeField.is_archived.is_(False))
    return list(session.scalars(statement))


def active_custom_fields(session: Session | None = None) -> list[EmployeeField]:
    return [
        field
        for field in ordered_fields(session, include_archived=False)
        if not field.is_system
    ]


def register_fields(session: Session | None = None) -> list[EmployeeField]:
    return [
        field
        for field in ordered_fields(session, include_archived=False)
        if field.show_in_register
    ]


def field_by_id(field_id, session: Session | None = None) -> EmployeeField | None:
    """Return one field from the request catalog when that catalog is already loaded."""
    session = session_or_default(session)
    if _cacheable(session):
        for field in ordered_fields(session, include_archived=True):
            if field.id == field_id:
                return field
        return None
    return session.get(EmployeeField, field_id)


def create_custom_field(actor_id, values: dict, option_labels: list[str]) -> EmployeeField:
    ensure_system_fields(commit=False)
    _reject_duplicate_label(values["label"], values["section"], None)
    if values["field_type"] not in CUSTOM_TYPE_KEYS:
        raise FieldError("field_type", "Choose a supported field type.")
    if values["field_type"] == "dropdown" and not option_labels:
        raise FieldError("options", "Add at least one dropdown option.")
    current = db.session.scalar(select(func.max(EmployeeField.sort_order))) or 0
    values = dict(values)
    values["sort_order"] = current + 10
    field = EmployeeField(
        id=new_id(),
        field_key=_new_key(),
        version=1,
        is_system=False,
        is_archived=False,
        **values,
    )
    db.session.add(field)
    _replace_new_options(field, option_labels)
    _bump()
    record_audit(actor_id, "employee_field", field.id, "create", {}, column_snapshot(field, FIELD_SNAPSHOT))
    commit_or_conflict("label")
    return field


def update_custom_field(actor_id, field_id, submitted_version: int, values: dict, option_labels: list[str] | None = None) -> EmployeeField:
    field = reload_locked(EmployeeField, field_id)
    if field.version != submitted_version:
        from app.domain import StaleEditError

        db.session.rollback()
        raise StaleEditError()
    _reject_duplicate_label(values["label"], values["section"], field.id)
    if field.is_system:
        values = {
            "label": values["label"],
            "help_text": values["help_text"],
            "placeholder": values["placeholder"],
            "section": values["section"],
            "show_in_register": values["show_in_register"],
        }
    else:
        if values["field_type"] not in CUSTOM_TYPE_KEYS:
            raise FieldError("field_type", "Choose a supported field type.")
        if values["field_type"] != field.field_type:
            _reject_incompatible_type(field, values["field_type"])
        if option_labels:
            _append_options(field, option_labels)
        if values["field_type"] == "dropdown" and not _active_options(field):
            raise FieldError("options", "Add at least one dropdown option before saving.")
    before = column_snapshot(field, FIELD_SNAPSHOT)
    for key, value in values.items():
        setattr(field, key, value)
    after = column_snapshot(field, FIELD_SNAPSHOT)
    from app.audit import changes_between

    if not changes_between(before, after):
        db.session.rollback()
        return field
    field.version += 1
    _bump()
    record_audit(actor_id, "employee_field", field.id, "update", before, after)
    commit_or_conflict("label")
    return field


def move_field(actor_id, field_id, direction: int) -> None:
    fields = ordered_fields()
    index = next((position for position, field in enumerate(fields) if field.id == field_id), None)
    if index is None:
        from flask import abort

        abort(404)
    target = index + direction
    if target < 0 or target >= len(fields):
        return
    current = fields[index]
    other = fields[target]
    current.sort_order, other.sort_order = other.sort_order, current.sort_order
    if current.sort_order == other.sort_order:
        current.sort_order = index
        other.sort_order = target
    _bump()
    record_audit(
        actor_id,
        "employee_field",
        current.id,
        "update",
        {"sort_order": other.sort_order},
        {"sort_order": current.sort_order},
    )
    commit_or_conflict("label")


def set_archived(actor_id, field_id, archived: bool) -> EmployeeField:
    field = reload_locked(EmployeeField, field_id)
    if field.is_system:
        db.session.rollback()
        raise FieldError("label", "Employee name, facility, and mobile number cannot be archived.")
    if field.is_archived == archived:
        db.session.rollback()
        return field
    before = {"is_archived": field.is_archived, "label": field.label}
    field.is_archived = archived
    field.version += 1
    _bump()
    record_audit(
        actor_id,
        "employee_field",
        field.id,
        "update",
        before,
        {"is_archived": archived, "label": field.label},
    )
    commit_or_conflict("label")
    return field


def _remove_custom_key(key: str) -> None:
    """Remove one custom-data key in SQL. Empty values are removed and not counted."""
    if db.engine.dialect.name == "postgresql":
        has_key = func.jsonb_exists(Employee.custom_data, bindparam("drop_key", key))
        stripped = Employee.custom_data.op("-")(bindparam("drop_key_value", key))
        db.session.execute(update(Employee).where(has_key).values(custom_data=stripped))
        return
    path_where = bindparam("drop_path_where", f"$.{key}")
    path_set = bindparam("drop_path_set", f"$.{key}")
    present = func.json_extract(Employee.custom_data, path_where).is_not(None)
    db.session.execute(
        update(Employee).where(present).values(custom_data=func.json_remove(Employee.custom_data, path_set))
    )


def delete_custom_field(actor_id, field_id, submitted_version: int, confirmed_label: str) -> int:
    field = reload_locked(EmployeeField, field_id)
    if field.is_system:
        db.session.rollback()
        raise FieldError("confirm_label", "Employee name, facility, and mobile number cannot be deleted.")
    if field.version != submitted_version:
        from app.domain import StaleEditError

        db.session.rollback()
        raise StaleEditError()
    if confirmed_label != field.label:
        db.session.rollback()
        raise FieldError("confirm_label", "Type the field label exactly to confirm deletion.")
    key = str(field.id)
    affected = stored_value_count(field)
    _remove_custom_key(key)
    label = field.label
    field_key = field.field_key
    db.session.delete(field)
    _bump()
    record_audit(
        actor_id,
        "employee_field",
        field_id,
        "delete",
        {"label": label, "field_key": field_key, "affected_records": affected},
        {},
    )
    commit_or_conflict("label")
    return affected


def add_option(actor_id, field_id, label: str) -> EmployeeFieldOption:
    field = reload_locked(EmployeeField, field_id)
    if field.is_system or field.field_type != "dropdown":
        db.session.rollback()
        raise FieldError("option_label", "Options belong on a dropdown field.")
    label = (label or "").strip()
    if not label or len(label) > 120:
        db.session.rollback()
        raise FieldError("option_label", "Enter an option label of up to 120 characters.")
    if any(option.label.casefold() == label.casefold() and not option.is_retired for option in field.options):
        db.session.rollback()
        raise FieldError("option_label", "That option already exists.")
    option = EmployeeFieldOption(
        id=new_id(),
        field_id=field.id,
        label=label,
        sort_order=len(field.options) + 1,
        is_retired=False,
    )
    db.session.add(option)
    _bump()
    record_audit(actor_id, "employee_field", field.id, "update", {}, {"option_added": label})
    commit_or_conflict("option_label")
    return option


def retire_option(actor_id, field_id, option_id) -> None:
    field = reload_locked(EmployeeField, field_id)
    option = db.session.get(EmployeeFieldOption, option_id)
    if option is None or option.field_id != field.id:
        db.session.rollback()
        from flask import abort

        abort(404)
    if option.is_retired:
        db.session.rollback()
        return
    option.is_retired = True
    _bump()
    record_audit(
        actor_id,
        "employee_field",
        field.id,
        "update",
        {"option": option.label, "retired": False},
        {"option": option.label, "retired": True},
    )
    commit_or_conflict("option_label")


def delete_option(actor_id, field_id, option_id) -> None:
    field = reload_locked(EmployeeField, field_id)
    option = db.session.get(EmployeeFieldOption, option_id)
    if option is None or option.field_id != field.id:
        db.session.rollback()
        from flask import abort

        abort(404)
    if _option_in_use(field, option.id):
        db.session.rollback()
        raise FieldError("option_label", "This option is used by employee records. Retire it instead of deleting it.")
    label = option.label
    db.session.delete(option)
    _bump()
    record_audit(actor_id, "employee_field", field.id, "update", {"option_deleted": label}, {})
    commit_or_conflict("option_label")


def _missing_clause(field: EmployeeField, suffix: str = "missing"):
    if field.is_system and field.field_key == "phone":
        return func.coalesce(Employee.phone, "") == ""
    if field.is_system and field.field_key == "facility_id":
        return Employee.facility_id.is_(None)
    if field.is_system and field.field_key == "full_name":
        return func.coalesce(Employee.full_name, "") == ""
    if field.is_system:
        return false()
    return _custom_missing_clause(field, suffix)


def _count_matching(criterion):
    return func.coalesce(func.sum(case((criterion, 1), else_=0)), 0)


def missing_value_counts(fields: list[EmployeeField]) -> dict:
    """Count missing required values for every requested field in one query."""
    required = [field for field in fields if field.required and not field.is_archived]
    if not required:
        return {}
    row = db.session.execute(
        select(*[_count_matching(_missing_clause(field)) for field in required]).select_from(Employee)
    ).one()
    return {field.id: int(value or 0) for field, value in zip(required, row)}


def missing_value_count(field: EmployeeField) -> int:
    if not field.required or field.is_archived:
        return 0
    return missing_value_counts([field]).get(field.id, 0)


def stored_value_count(field: EmployeeField) -> int:
    value = json_text(str(field.id), "stored")
    present = and_(value.is_not(None), cast(value, String) != "")
    return db.session.scalar(select(func.count()).select_from(Employee).where(present)) or 0


def parse_custom_values(form_data, fields: list[EmployeeField], existing: dict | None) -> tuple[dict, dict]:
    current = dict(existing or {})
    errors = {}
    for field in fields:
        raw = form_data.get(f"custom_{field.id}", "")
        if isinstance(raw, str):
            raw = raw.strip()
        try:
            parsed = _parse_value(field, raw, current.get(str(field.id)))
        except ValueError as exc:
            errors[str(field.id)] = str(exc)
            current[str(field.id)] = raw
            continue
        if parsed is None:
            current.pop(str(field.id), None)
        else:
            current[str(field.id)] = parsed
    return current, errors


def display_custom(field: EmployeeField, value) -> str:
    if value in (None, ""):
        return "—"
    if is_aadhaar_field(field):
        digits = re.sub(r"\D", "", str(value))
        if len(digits) == 12:
            return f"{digits[0:4]} {digits[4:8]} {digits[8:12]}"
        return str(value)
    if field.field_type == "yes_no":
        return "Yes" if value == "yes" else "No"
    if field.field_type == "dropdown":
        option = next((item for item in field.options if str(item.id) == str(value)), None)
        if option is None:
            return "—"
        return f"{option.label} (retired)" if option.is_retired else option.label
    return str(value)


def incomplete_predicate(suffix: str = "missing"):
    """Match an employee who is missing at least one active required value.

    Numeric 0 and yes/no False or "no" are present. Archived fields are ignored.
    """
    ensure_system_fields()
    criteria = [
        _missing_clause(field, suffix)
        for field in ordered_fields(include_archived=False)
        if field.required and not (field.is_system and field.field_key not in {"phone", "facility_id", "full_name"})
    ]
    if not criteria:
        return None
    return or_(*criteria)


def incomplete_employee_count() -> int:
    if has_request_context() and hasattr(g, "_hrms_incomplete_count"):
        return g._hrms_incomplete_count
    predicate = incomplete_predicate()
    if predicate is None:
        count = 0
    else:
        count = db.session.scalar(select(func.count()).select_from(Employee).where(predicate)) or 0
    if has_request_context():
        g._hrms_incomplete_count = count
    return count


def remember_incomplete_count(count: int) -> None:
    if has_request_context():
        g._hrms_incomplete_count = count


def incomplete_employee_ids() -> list:
    if has_request_context() and hasattr(g, "_hrms_incomplete_ids"):
        return g._hrms_incomplete_ids
    predicate = incomplete_predicate()
    if predicate is None:
        found = []
    else:
        found = list(db.session.scalars(select(Employee.id).where(predicate)))
    if has_request_context():
        g._hrms_incomplete_ids = found
    return found


def json_text(field_id: str, suffix: str = "value"):
    """Return a SQL expression for one custom value.

    The field id is bound as a query parameter. It is not concatenated into the SQL text.
    """
    key = str(field_id)
    token = key.replace("-", "")
    if db.engine.dialect.name == "postgresql":
        return Employee.custom_data.op("->>")(bindparam(f"jk_{suffix}_{token}", key))
    return func.json_extract(Employee.custom_data, bindparam(f"jp_{suffix}_{token}", f"$.{key}"))


def backfill_employee_facilities(session: Session | None = None) -> int:
    """Copy an open posting's facility onto an employee that does not have one.

    Employees without an open posting are left unchanged. Phone numbers are never invented.
    """
    session = session_or_default(session)
    updated = 0
    employees = session.scalars(select(Employee).where(Employee.facility_id.is_(None))).all()
    for employee in employees:
        posting = session.scalar(
            select(Posting).where(Posting.employee_id == employee.id, Posting.end_date.is_(None)).limit(1)
        )
        if posting is None:
            continue
        employee.facility_id = posting.facility_id
        updated += 1
    session.flush()
    return updated


def split_options(raw: str) -> list[str]:
    labels = []
    for line in (raw or "").splitlines():
        label = line.strip()
        if not label:
            continue
        if len(label) > 120:
            raise FieldError("options", "Each dropdown option must be 120 characters or fewer.")
        if label.casefold() in {item.casefold() for item in labels}:
            raise FieldError("options", "Dropdown options must be unique.")
        labels.append(label)
    return labels


def _whole_number(raw) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError("Enter a whole number.")
    if isinstance(raw, int):
        return raw
    if not re.fullmatch(r"[+-]?[0-9]+", raw):
        raise ValueError("Enter a whole number.")
    return int(raw)


def _decimal_number(raw) -> Decimal:
    if isinstance(raw, bool):
        raise ValueError("Enter a decimal with up to 12 digits and 4 decimal places.")
    if isinstance(raw, Decimal):
        text = format(raw, "f")
    elif isinstance(raw, int):
        text = str(raw)
    elif isinstance(raw, str):
        text = raw
    else:
        raise ValueError("Enter a decimal with up to 12 digits and 4 decimal places.")
    if not re.fullmatch(r"[+-]?[0-9]{1,12}(\.[0-9]{1,4})?", text):
        raise ValueError("Enter a decimal with up to 12 digits and 4 decimal places.")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("Enter a decimal number.") from exc


def _parse_value(field: EmployeeField, raw, existing):
    if isinstance(raw, str):
        raw = raw.strip()
    # 0 and False are stored values. Only a missing value or an empty string is blank.
    if raw is None or raw == "":
        if field.required:
            raise ValueError("This field is required.")
        return None
    if is_aadhaar_field(field):
        message = aadhaar_problem(raw)
        if message:
            raise ValueError(message)
        return aadhaar_digits(raw)
    if field.field_type == "short_text":
        if len(raw) > SHORT_LIMIT:
            raise ValueError(f"Use at most {SHORT_LIMIT} characters.")
        return raw
    if field.field_type == "long_text":
        if len(raw) > LONG_LIMIT:
            raise ValueError(f"Use at most {LONG_LIMIT} characters.")
        return raw
    if field.field_type == "integer":
        number = _whole_number(raw)
        if number < -1_000_000_000 or number > 1_000_000_000:
            raise ValueError("Enter a whole number between -1000000000 and 1000000000.")
        return number
    if field.field_type == "decimal":
        parsed = _decimal_number(raw)
        rendered = format(parsed, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered
    if field.field_type == "date":
        try:
            parsed = date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("Enter a valid date.") from exc
        if parsed < date(1900, 1, 1) or parsed > date(2100, 12, 31):
            raise ValueError("Enter a date between 1900 and 2100.")
        return parsed.isoformat()
    if field.field_type == "yes_no":
        if raw is True or raw == "yes":
            return "yes"
        if raw is False or raw == "no":
            return "no"
        raise ValueError("Choose yes or no.")
    if field.field_type == "dropdown":
        try:
            option_id = uuid.UUID(str(raw))
        except ValueError as exc:
            raise ValueError("Choose an option from the list.") from exc
        option = next((item for item in field.options if item.id == option_id), None)
        if option is None:
            raise ValueError("Choose an option from the list.")
        if option.is_retired and str(existing) != str(option.id):
            raise ValueError("That option is retired. Choose an active option.")
        return str(option.id)
    raise ValueError("This field type is not supported.")


def _reject_incompatible_type(field: EmployeeField, new_type: str) -> None:
    if field.field_type == "dropdown" and new_type != "dropdown" and stored_value_count(field):
        raise FieldError(
            "field_type",
            "Cannot change a dropdown that already has selections. Existing choices were left unchanged.",
        )
    incompatible = 0
    for data in db.session.scalars(select(Employee.custom_data)):
        if not isinstance(data, dict):
            continue
        value = data.get(str(field.id))
        if value in (None, ""):
            continue
        try:
            _parse_stored(new_type, value, field)
        except ValueError:
            incompatible += 1
    if incompatible:
        label = dict(CUSTOM_TYPES).get(new_type, new_type)
        raise FieldError(
            "field_type",
            f"Cannot change this field to {label}. {incompatible} existing value"
            f"{'' if incompatible == 1 else 's'} would not fit, so nothing was changed.",
        )


def _parse_stored(new_type: str, value, field: EmployeeField) -> None:
    if new_type in {"short_text", "long_text"}:
        text = str(value)
        limit = SHORT_LIMIT if new_type == "short_text" else LONG_LIMIT
        if len(text) > limit:
            raise ValueError
        return
    if new_type == "integer":
        _parse_value(_typed(field, "integer"), str(value), None)
        return
    if new_type == "decimal":
        _parse_value(_typed(field, "decimal"), str(value), None)
        return
    if new_type == "date":
        _parse_value(_typed(field, "date"), str(value), None)
        return
    if new_type == "yes_no":
        if value not in {"yes", "no"}:
            raise ValueError
        return
    if new_type == "dropdown":
        _parse_value(_typed(field, "dropdown"), str(value), value)
        return
    raise ValueError


class _typed:
    def __init__(self, field, field_type):
        self.options = field.options
        self.required = False
        self.field_type = field_type


def _reject_duplicate_label(label: str, section: str, exclude_id) -> None:
    label_key = label.casefold()
    section_key = section.casefold()
    for field in ordered_fields(include_archived=False):
        if exclude_id is not None and field.id == exclude_id:
            continue
        if field.label.casefold() == label_key and field.section.casefold() == section_key:
            raise FieldError("label", "An active field in this section already uses that label.")


def _append_options(field: EmployeeField, labels: list[str]) -> None:
    existing = {option.label.casefold() for option in field.options if not option.is_retired}
    start = len(field.options)
    for offset, label in enumerate(labels, start=1):
        if label.casefold() in existing:
            raise FieldError("options", "Dropdown options must be unique.")
        existing.add(label.casefold())
        field.options.append(
            EmployeeFieldOption(id=new_id(), label=label, sort_order=start + offset, is_retired=False)
        )


def _replace_new_options(field: EmployeeField, labels: list[str]) -> None:
    if field.field_type != "dropdown":
        return
    for index, label in enumerate(labels, start=1):
        field.options.append(
            EmployeeFieldOption(id=new_id(), label=label, sort_order=index, is_retired=False)
        )


def _active_options(field: EmployeeField) -> list[EmployeeFieldOption]:
    return [option for option in field.options if not option.is_retired]


def _option_in_use(field: EmployeeField, option_id) -> bool:
    value = cast(json_text(str(field.id), "option"), String)
    return db.session.scalar(select(Employee.id).where(value == str(option_id)).limit(1)) is not None


def _custom_missing_clause(field: EmployeeField, suffix: str = "missing"):
    value = json_text(str(field.id), suffix)
    return func.coalesce(cast(value, String), "") == ""


def _bump() -> None:
    catalog = db.session.get(FieldCatalog, 1)
    catalog.version += 1


def _new_key() -> str:
    key = f"cf_{uuid.uuid4().hex}"
    if key in RESERVED_KEYS or not KEY_RE.fullmatch(key):
        raise FieldError("label", "Could not assign a field key.")
    return key
