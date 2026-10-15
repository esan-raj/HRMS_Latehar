from flask import abort, flash, render_template
from sqlalchemy import select

from app.extensions import db
from app.models import Employee, Posting

SECTIONS = (
    ("personal", "Personal Details", "employees.personal"),
    ("employment", "Employment", "employees.employment"),
    ("postings", "Current Posting", "employees.postings"),
)


def get_employee(employee_id) -> Employee:
    employee = db.session.get(Employee, employee_id)
    if employee is None:
        abort(404)
    return employee


def owned_record(model, employee_id, record_id):
    row = db.session.get(model, record_id)
    if row is None or getattr(row, "employee_id", None) != employee_id:
        abort(404)
    return row


def current_posting(employee_id):
    return db.session.scalar(
        select(Posting)
        .where(Posting.employee_id == employee_id, Posting.end_date.is_(None))
        .limit(1)
    )


def render_employee(template: str, employee: Employee, section: str, status: int = 200, **kwargs):
    body = render_template(
        template,
        employee=employee,
        section=section,
        sections=SECTIONS,
        current_posting=current_posting(employee.id),
        **kwargs,
    )
    return body, status


def apply_field_error(form, exc) -> None:
    field = getattr(form, exc.field, None)
    if field is None:
        flash(exc.message, "danger")
    else:
        field.errors.append(exc.message)
