from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from app.choices import active_choices, facility_label, register_filter_choices
from app.directory import employee_directory
from app.domain import FieldError, StaleEditError, StaleFormError
from app.extensions import db
from app.fielddefs import (
    active_custom_fields,
    catalog_version,
    ensure_system_fields,
    load_dropdown_options,
    ordered_fields,
    parse_custom_values,
)
from app.forms import EmploymentForm, PersonalForm
from app.hr import create_employee, next_employee_code, save_employment, set_employee_active, update_employee
from app.models import Department, EmploymentDetail, EmploymentType, Facility
from app.web import apply_field_error, get_employee, render_employee

bp = Blueprint("employees", __name__, url_prefix="/employees")


@bp.route("/")
def index():
    page, filters, sort, direction, columns, incomplete_count = employee_directory(request.args)
    designations, facilities, employment_types = register_filter_choices()
    return render_template(
        "employees/list.html",
        page=page,
        filters=filters,
        sort=sort,
        direction=direction,
        columns=columns,
        query=filters.get("q", ""),
        status=filters.get("status", ""),
        designation_id=filters.get("designation_id", ""),
        facility_id=filters.get("facility_id", ""),
        employment_type_id=filters.get("employment_type_id", ""),
        incomplete=filters.get("incomplete", ""),
        filter_field=filters.get("filter_field", ""),
        filter_value=filters.get("filter_value", ""),
        incomplete_count=incomplete_count,
        filterable_fields=_filterable_fields(),
        designations=designations,
        facilities=facilities,
        employment_types=employment_types,
    )


@bp.route("/new", methods=["GET", "POST"])
def create():
    form = PersonalForm()
    custom_fields = _prepare_employee_form(form, None)
    custom_values = _posted_custom(custom_fields, {})
    custom_errors = {}
    if form.validate_on_submit():
        try:
            _check_form_version(form)
            custom_values, custom_errors = parse_custom_values(request.form, custom_fields, {})
            if custom_errors:
                return _employee_form(form, None, custom_fields, custom_values, custom_errors, 400)
            values = form.values()
            values.pop("employee_code", None)
            values["custom_data"] = custom_values
            employee = create_employee(g.actor_id, values)
        except StaleFormError as exc:
            flash(exc.message, "warning")
            custom_fields = _prepare_employee_form(form, None)
            form.fields_version.data = catalog_version()
            return _employee_form(form, None, custom_fields, _posted_custom(custom_fields, {}), {}, 409)
        except FieldError as exc:
            apply_field_error(form, exc)
            return _employee_form(form, None, custom_fields, custom_values, custom_errors, 400)
        flash(f"Employee {employee.employee_code} created.", "success")
        return redirect(url_for("employees.personal", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return _employee_form(form, None, custom_fields, custom_values, custom_errors, status)


@bp.route("/<uuid:employee_id>")
def profile(employee_id):
    get_employee(employee_id)
    return redirect(url_for("employees.personal", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/personal", methods=["GET", "POST"])
def personal(employee_id):
    employee = get_employee(employee_id)
    form = PersonalForm()
    custom_fields = _prepare_employee_form(form, employee)
    custom_values = _posted_custom(custom_fields, employee.custom_data or {})
    custom_errors = {}
    if form.validate_on_submit():
        try:
            _check_form_version(form)
            custom_values, custom_errors = parse_custom_values(request.form, custom_fields, employee.custom_data or {})
            if custom_errors:
                return _personal(employee, form, custom_fields, custom_values, custom_errors, 400)
            values = form.values()
            values["custom_data"] = custom_values
            _employee, changed = update_employee(g.actor_id, employee.id, form.version.data, values)
        except StaleFormError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            form = PersonalForm()
            custom_fields = _prepare_employee_form(form, employee)
            flash(exc.message, "warning")
            form.fields_version.data = catalog_version()
            return _personal(employee, form, custom_fields, _posted_custom(custom_fields, employee.custom_data or {}), {}, 409)
        except FieldError as exc:
            apply_field_error(form, exc)
            return _personal(employee, form, custom_fields, custom_values, custom_errors, 400)
        except StaleEditError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            form = PersonalForm()
            custom_fields = _prepare_employee_form(form, employee)
            flash(exc.message, "warning")
            return _personal(
                employee,
                form,
                custom_fields,
                _posted_custom(custom_fields, employee.custom_data or {}),
                {},
                409,
            )
        flash("Personal details saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.personal", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return _personal(employee, form, custom_fields, custom_values, custom_errors, status)


@bp.route("/<uuid:employee_id>/employment", methods=["GET", "POST"])
def employment(employee_id):
    employee = get_employee(employee_id)
    detail = _employment(employee.id)
    form = EmploymentForm()
    _prepare_employment(form, detail, request.method == "GET")
    if form.validate_on_submit():
        try:
            _row, changed = save_employment(g.actor_id, employee.id, form.version.data, form.values())
        except FieldError as exc:
            apply_field_error(form, exc)
            return _render_employment(employee, form, 400)
        except StaleEditError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            detail = _employment(employee.id)
            form = EmploymentForm()
            _prepare_employment(form, detail, True)
            flash(exc.message, "warning")
            return _render_employment(employee, form, 409)
        flash("Employment details saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.employment", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return _render_employment(employee, form, status)


@bp.route("/<uuid:employee_id>/deactivate", methods=["GET", "POST"])
def deactivate(employee_id):
    return _set_active(employee_id, False)


@bp.route("/<uuid:employee_id>/reactivate", methods=["GET", "POST"])
def reactivate(employee_id):
    return _set_active(employee_id, True)


def _set_active(employee_id, active: bool):
    employee = get_employee(employee_id)
    action = "Reactivate" if active else "Deactivate"
    if request.method == "GET":
        return render_template(
            "confirm.html",
            title=f"{action} employee",
            message=f"{action} {employee.full_name} ({employee.employee_code})? Service history is kept.",
            action=url_for(request.endpoint, employee_id=employee.id),
            confirm_label=action,
            cancel_url=url_for("employees.personal", employee_id=employee.id),
            version=employee.version,
        )
    version = request.form.get("version", type=int)
    if version is None:
        abort(400)
    try:
        _employee, changed = set_employee_active(g.actor_id, employee.id, version, active)
    except StaleEditError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("employees.personal", employee_id=employee.id))
    if changed:
        flash(f"Employee {action.lower()}d.", "success")
    else:
        flash(f"Employee is already {action.lower()}d.", "info")
    return redirect(url_for("employees.personal", employee_id=employee.id))


def _prepare_employee_form(form, employee):
    ensure_system_fields()
    selected = employee.facility_id if employee is not None else None
    if request.method == "POST":
        selected = request.form.get("facility_id") or selected
    form.facility_id.choices = active_choices(Facility, selected=selected, label=facility_label) or [
        ("", "Add a facility first")
    ]
    by_key = {field.field_key: field for field in ordered_fields(include_archived=False)}
    for key, attr in (("full_name", "full_name"), ("phone", "phone"), ("facility_id", "facility_id")):
        definition = by_key.get(key)
        if definition is None:
            continue
        target = getattr(form, attr)
        target.label.text = definition.label
        target.description = definition.help_text or ""
        if definition.placeholder:
            target.render_kw = {**(target.render_kw or {}), "placeholder": definition.placeholder}
    if request.method == "GET":
        if employee is not None:
            form.process(obj=employee)
            form.facility_id.data = str(employee.facility_id) if employee.facility_id else ""
            form.version.data = employee.version
        else:
            form.version.data = 0
            form.is_active.data = True
        form.fields_version.data = catalog_version()
    custom_fields = active_custom_fields()
    load_dropdown_options(custom_fields)
    return custom_fields


def _check_form_version(form) -> None:
    if form.fields_version.data != catalog_version():
        raise StaleFormError()


def _posted_custom(fields, stored: dict) -> dict:
    if request.method == "POST":
        return {str(field.id): request.form.get(f"custom_{field.id}", "") for field in fields}
    values = {}
    for field in fields:
        current = (stored or {}).get(str(field.id))
        values[str(field.id)] = "" if current is None else str(current)
    return values


def _employee_form(form, employee, custom_fields, custom_values, custom_errors, status):
    return (
        render_template(
            "employees/form.html",
            form=form,
            employee=employee,
            custom_fields=custom_fields,
            custom_values=custom_values,
            custom_errors=custom_errors,
            form_fields=_form_fields(),
            next_code=next_employee_code(),
        ),
        status,
    )


def _personal(employee, form, custom_fields, custom_values, custom_errors, status):
    return render_employee(
        "employees/personal.html",
        employee,
        "personal",
        form=form,
        custom_fields=custom_fields,
        custom_values=custom_values,
        custom_errors=custom_errors,
        form_fields=_form_fields(),
        status=status,
    )


def _form_fields():
    return ordered_fields(include_archived=False)


def _filterable_fields():
    from app.fielddefs import FILTERABLE_TYPES

    return [
        field
        for field in ordered_fields(include_archived=False)
        if not field.is_system and field.field_type in FILTERABLE_TYPES and field.show_in_register
    ]


def _render_employment(employee, form, status, appointment_form=None, editing=None):
    from app.record_routes import appointment_context

    context = appointment_context(employee, appointment_form, editing)
    return render_employee("employees/employment.html", employee, "employment", form=form, status=status, **context)


def _employment(employee_id):
    return db.session.scalar(select(EmploymentDetail).where(EmploymentDetail.employee_id == employee_id))


def _prepare_employment(form, detail, bind: bool) -> None:
    form.department_id.choices = active_choices(Department, selected=detail.department_id if detail else None)
    form.employment_type_id.choices = active_choices(
        EmploymentType, selected=detail.employment_type_id if detail else None
    )
    if not bind:
        return
    if detail is None:
        form.version.data = 0
        return
    form.department_id.data = str(detail.department_id)
    form.employment_type_id.data = str(detail.employment_type_id)
    form.service_joining_date.data = detail.service_joining_date
    form.retirement_date.data = detail.retirement_date
    form.pay_level.data = detail.pay_level
    form.version.data = detail.version
