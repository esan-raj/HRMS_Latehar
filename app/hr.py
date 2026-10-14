from datetime import date

from sqlalchemy import func, select

from app.audit import column_snapshot, record_audit
from app.domain import FieldError, StaleEditError
from app.extensions import db
from app.models import (
    Address,
    Allegation,
    DacpDetail,
    Department,
    Designation,
    EducationRecord,
    Employee,
    EmploymentDetail,
    EmploymentType,
    Facility,
    JobAppointment,
    LeaveRecord,
    LeaveType,
    Posting,
    Promotion,
    Qualification,
    TrainingRecord,
    TrainingType,
)
from app.persistence import apply_versioned_update, commit_or_conflict, new_id, reload_locked

EMPLOYEE_FIELDS = (
    "employee_code",
    "full_name",
    "date_of_birth",
    "gender",
    "phone",
    "email",
    "facility_id",
    "custom_data",
    "is_active",
)
ADDRESS_FIELDS = ("address_type", "line1", "line2", "district", "state", "pin_code")
EMPLOYMENT_FIELDS = (
    "department_id",
    "employment_type_id",
    "service_joining_date",
    "retirement_date",
    "pay_level",
)
POSTING_FIELDS = ("facility_id", "designation_id", "start_date", "end_date", "order_reference", "remarks")


def create_employee(actor_id, values: dict) -> Employee:
    _reject_duplicate_code(values["employee_code"], None)
    values = dict(values)
    values.setdefault("custom_data", {})
    _require_employee_basics(values)
    employee = Employee(id=new_id(), version=1, **values)
    db.session.add(employee)
    record_audit(actor_id, "employee", employee.id, "create", {}, column_snapshot(employee, EMPLOYEE_FIELDS))
    commit_or_conflict("employee_code")
    return employee


def update_employee(actor_id, employee_id, submitted_version: int, values: dict):
    employee = reload_locked(Employee, employee_id)
    _require_employee_basics(values)
    _reject_duplicate_code(values["employee_code"], employee.id)
    before, after, changed = apply_versioned_update(employee, submitted_version, values, EMPLOYEE_FIELDS)
    if not changed:
        db.session.rollback()
        return employee, False
    record_audit(actor_id, "employee", employee.id, "update", before, after)
    commit_or_conflict("employee_code")
    return employee, True


def set_employee_active(actor_id, employee_id, submitted_version: int, active: bool) -> Employee:
    employee = reload_locked(Employee, employee_id)
    if employee.version != submitted_version:
        db.session.rollback()
        raise StaleEditError()
    if employee.is_active == active:
        db.session.rollback()
        return employee, False
    before, after, _changed = apply_versioned_update(
        employee,
        submitted_version,
        {"is_active": active},
        EMPLOYEE_FIELDS,
    )
    record_audit(
        actor_id,
        "employee",
        employee.id,
        "reactivate" if active else "deactivate",
        before,
        after,
    )
    commit_or_conflict("employee_code")
    return employee, True


def save_addresses(actor_id, employee_id, present: dict, present_version: int, permanent: dict, permanent_version: int):
    reload_locked(Employee, employee_id)
    changed = _save_address(actor_id, employee_id, "present", present, present_version)
    changed = _save_address(actor_id, employee_id, "permanent", permanent, permanent_version) or changed
    if not changed:
        db.session.rollback()
        return False
    commit_or_conflict("present_line1")
    return True


def save_employment(actor_id, employee_id, submitted_version: int, values: dict):
    reload_locked(Employee, employee_id)
    row = db.session.scalar(
        select(EmploymentDetail)
        .where(EmploymentDetail.employee_id == employee_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        if submitted_version not in (0, None):
            db.session.rollback()
            raise StaleEditError()
        row = EmploymentDetail(id=new_id(), employee_id=employee_id, version=1, **values)
        db.session.add(row)
        record_audit(actor_id, "employment_detail", row.id, "create", {}, column_snapshot(row, EMPLOYMENT_FIELDS))
        commit_or_conflict("department_id")
        return row, True
    before, after, changed = apply_versioned_update(row, submitted_version, values, EMPLOYMENT_FIELDS)
    if not changed:
        db.session.rollback()
        return row, False
    record_audit(actor_id, "employment_detail", row.id, "update", before, after)
    commit_or_conflict("department_id")
    return row, True


def save_posting(actor_id, employee_id, record_id, submitted_version: int, values: dict):
    reload_locked(Employee, employee_id)
    assert_posting_period(employee_id, values["start_date"], values.get("end_date"), record_id)
    if record_id is None:
        if submitted_version not in (0, None):
            db.session.rollback()
            raise StaleEditError()
        row = Posting(id=new_id(), employee_id=employee_id, version=1, **values)
        db.session.add(row)
        record_audit(actor_id, "posting", row.id, "create", {}, column_snapshot(row, POSTING_FIELDS))
        commit_or_conflict("end_date")
        return row, True
    row = reload_locked(Posting, record_id)
    if row.employee_id != employee_id:
        db.session.rollback()
        from flask import abort

        abort(404)
    before, after, changed = apply_versioned_update(row, submitted_version, values, POSTING_FIELDS)
    if not changed:
        db.session.rollback()
        return row, False
    record_audit(actor_id, "posting", row.id, "update", before, after)
    commit_or_conflict("end_date")
    return row, True


def assert_posting_period(employee_id, start: date, end: date | None, exclude_id):
    if end is not None and end < start:
        db.session.rollback()
        raise FieldError("end_date", "End date cannot be before the start date.")
    statement = select(Posting).where(Posting.employee_id == employee_id)
    if exclude_id is not None:
        statement = statement.where(Posting.id != exclude_id)
    rows = db.session.scalars(statement).all()
    if end is None and any(row.end_date is None for row in rows):
        db.session.rollback()
        raise FieldError(
            "end_date",
            "This employee already has an open posting. End that posting before opening another.",
        )
    for row in rows:
        if _overlaps(start, end, row.start_date, row.end_date):
            db.session.rollback()
            raise FieldError("start_date", "This period overlaps an existing posting.")


def create_child(actor_id, employee_id, model, entity_type: str, fields: tuple[str, ...], values: dict):
    reload_locked(Employee, employee_id)
    row = model(id=new_id(), employee_id=employee_id, version=1, **values)
    db.session.add(row)
    record_audit(actor_id, entity_type, row.id, "create", {}, column_snapshot(row, fields))
    commit_or_conflict("form")
    return row


def update_child(actor_id, employee_id, model, record_id, entity_type: str, fields: tuple[str, ...], submitted_version: int, values: dict):
    reload_locked(Employee, employee_id)
    row = reload_locked(model, record_id)
    if row.employee_id != employee_id:
        db.session.rollback()
        from flask import abort

        abort(404)
    before, after, changed = apply_versioned_update(row, submitted_version, values, fields)
    if not changed:
        db.session.rollback()
        return row, False
    record_audit(actor_id, entity_type, row.id, "update", before, after)
    commit_or_conflict("form")
    return row, True


def delete_child(actor_id, employee_id, model, record_id, entity_type: str, fields: tuple[str, ...], submitted_version: int):
    reload_locked(Employee, employee_id)
    row = reload_locked(model, record_id)
    if row.employee_id != employee_id:
        db.session.rollback()
        from flask import abort

        abort(404)
    if row.version != submitted_version:
        db.session.rollback()
        raise StaleEditError()
    before = column_snapshot(row, fields)
    record_audit(actor_id, entity_type, row.id, "delete", before, {})
    db.session.delete(row)
    commit_or_conflict("form")


def _save_address(actor_id, employee_id, address_type: str, values: dict, submitted_version: int) -> bool:
    row = db.session.scalar(
        select(Address)
        .where(Address.employee_id == employee_id, Address.address_type == address_type)
        .execution_options(populate_existing=True)
    )
    payload = dict(values)
    payload["address_type"] = address_type
    if row is None:
        if submitted_version not in (0, None):
            db.session.rollback()
            raise StaleEditError()
        row = Address(id=new_id(), employee_id=employee_id, version=1, **payload)
        db.session.add(row)
        record_audit(actor_id, "address", row.id, "create", {}, column_snapshot(row, ADDRESS_FIELDS))
        return True
    before, after, changed = apply_versioned_update(row, submitted_version, payload, ADDRESS_FIELDS)
    if changed:
        record_audit(actor_id, "address", row.id, "update", before, after)
    return changed


def _require_employee_basics(values: dict) -> None:
    from app.forms import phone_problem

    name = str(values.get("full_name") or "").strip()
    if len(name) < 2 or not any(character.isalpha() for character in name):
        db.session.rollback()
        raise FieldError("full_name", "Enter an employee name of at least 2 characters, including letters.")
    message = phone_problem(values.get("phone") or "")
    if message:
        db.session.rollback()
        raise FieldError("phone", message)
    facility = db.session.get(Facility, values.get("facility_id"))
    if facility is None:
        db.session.rollback()
        raise FieldError("facility_id", "Choose a facility.")


def _reject_duplicate_code(code: str, exclude_id):
    statement = select(Employee.id).where(func.lower(Employee.employee_code) == code.lower())
    if exclude_id is not None:
        statement = statement.where(Employee.id != exclude_id)
    if db.session.scalar(statement) is not None:
        db.session.rollback()
        raise FieldError("employee_code", "An employee with this code already exists.")


def _overlaps(start_a: date, end_a: date | None, start_b: date, end_b: date | None) -> bool:
    end_a_value = end_a or date.max
    end_b_value = end_b or date.max
    return start_a <= end_b_value and start_b <= end_a_value


MASTER_REFERENCES = {
    "departments": ((EmploymentDetail, "department_id", "employment records"),),
    "designations": (
        (JobAppointment, "designation_id", "job appointments"),
        (Posting, "designation_id", "postings"),
        (Promotion, "previous_designation_id", "promotions"),
        (Promotion, "new_designation_id", "promotions"),
    ),
    "facilities": ((Posting, "facility_id", "postings"),),
    "employment-types": (
        (JobAppointment, "employment_type_id", "job appointments"),
        (EmploymentDetail, "employment_type_id", "employment records"),
    ),
    "qualifications": ((EducationRecord, "qualification_id", "education records"),),
    "training-types": ((TrainingRecord, "training_type_id", "training records"),),
    "leave-types": ((LeaveRecord, "leave_type_id", "leave records"),),
}

MASTER_MODELS = {
    "departments": Department,
    "designations": Designation,
    "facilities": Facility,
    "employment-types": EmploymentType,
    "qualifications": Qualification,
    "training-types": TrainingType,
    "leave-types": LeaveType,
}


def referenced_labels(slug: str, record_id) -> list[str]:
    labels = []
    for model, attr, label in MASTER_REFERENCES[slug]:
        column = getattr(model, attr)
        found = db.session.scalar(select(model.id).where(column == record_id).limit(1))
        if found is not None and label not in labels:
            labels.append(label)
    return labels


def delete_blocked_message(singular: str, labels: list[str]) -> str:
    if len(labels) == 1:
        used = labels[0]
    elif len(labels) == 2:
        used = f"{labels[0]} and {labels[1]}"
    else:
        used = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    return f"This {singular} is used by {used}. Deactivate it instead of deleting it."


def name_taken(model, name: str, exclude_id=None) -> bool:
    statement = select(model.id).where(func.lower(model.name) == name.lower())
    if exclude_id is not None:
        statement = statement.where(model.id != exclude_id)
    return db.session.scalar(statement) is not None


def facility_code_taken(code: str, exclude_id=None) -> bool:
    statement = select(Facility.id).where(Facility.code == code)
    if exclude_id is not None:
        statement = statement.where(Facility.id != exclude_id)
    return db.session.scalar(statement) is not None
