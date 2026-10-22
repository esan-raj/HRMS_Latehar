import io

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from werkzeug.exceptions import HTTPException
from sqlalchemy import select

from app.choices import active_choices, facility_label, register_filter_choices
from app.directory import employee_directory
from app.domain import FieldError, SelectionError, StaleEditError, StaleFormError
from app.extensions import db
from app.fielddefs import (
    active_custom_fields,
    catalog_version,
    ensure_system_fields,
    load_dropdown_options,
    ordered_fields,
    parse_custom_values,
)
from app.forms import GENDERS, EmploymentForm, PersonalForm
from app.hr import (
    create_employee,
    delete_employee,
    delete_employees,
    next_employee_code,
    preview_deletion,
    save_employment,
    set_employee_active,
    update_employee,
)
from app.models import Department, EmploymentDetail, EmploymentType, Facility
from app.sheet_pdf import table_pdf
from app.transfers import (
    EXPORT_LIMIT,
    BulkFileError,
    export_table,
    import_employees,
    record_facts,
    template_columns,
    template_csv,
    workbook_bytes,
)
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


REGISTER_FIELDS = (
    "q",
    "status",
    "designation_id",
    "facility_id",
    "employment_type_id",
    "filter_field",
    "filter_value",
    "incomplete",
    "sort",
    "dir",
    "page",
)


@bp.route("/delete-selected", methods=["POST"])
def delete_selected():
    selected = _selected_employees()
    return_args = _register_return_args()
    try:
        if request.form.get("confirm") != "yes":
            preview = preview_deletion(selected)
            return render_template(
                "employees/delete_selected.html",
                employees=preview,
                selected=selected,
                return_args=return_args,
            )
        codes = delete_employees(g.actor_id, selected)
    except SelectionError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("employees.index", **return_args))
    except FieldError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("employees.index", **return_args))
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        flash("The selected employees could not be deleted. Nothing was removed.", "danger")
        return redirect(url_for("employees.index", **return_args))
    flash(f"Deleted {len(codes)} employees: {', '.join(codes)}.", "success")
    return redirect(url_for("employees.index", **return_args))


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
    return redirect(url_for("employees.view_record", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/view")
def view_record(employee_id):
    employee = get_employee(employee_id)
    personal, custom, employment = record_facts(employee)
    return render_employee(
        "employees/view.html",
        employee,
        "view",
        personal=personal,
        custom=custom,
        employment=employment,
    )


@bp.route("/export/excel")
def export_xlsx():
    headers, rows, truncated = export_table(request.args)
    payload = workbook_bytes(headers, rows, truncated)
    return _download(payload, "employees.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/export/pdf")
def export_pdf():
    headers, rows, truncated = export_table(request.args)
    if request.args.get("employee_id"):
        note = "This PDF contains one employee."
    elif truncated:
        note = f"This PDF contains the first {EXPORT_LIMIT} filtered records, not only the current page."
    else:
        note = "This PDF contains all filtered records, not only the current page."
    payload = table_pdf(headers, rows, "Employees", note)
    return _download(payload, "employees.pdf", "application/pdf")


@bp.route("/bulk/template")
def bulk_template():
    return _download(template_csv(), "employee-template.csv", "text/csv; charset=utf-8")


@bp.route("/bulk", methods=["GET", "POST"])
def bulk():
    errors = []
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            errors = ["Choose a CSV file."]
        elif not upload.filename.lower().endswith(".csv"):
            errors = ["Upload a CSV file made from the sample template."]
        else:
            try:
                result = import_employees(g.actor_id, upload.read())
            except BulkFileError as exc:
                errors = [exc.message]
            else:
                if result.errors:
                    errors = result.errors
                else:
                    flash(f"Added {result.created} employees.", "success")
                    return redirect(url_for("employees.index"))
        return render_template("employees/bulk.html", columns=template_columns(), facilities=_active_facilities(), errors=errors), 400
    return render_template("employees/bulk.html", columns=template_columns(), facilities=_active_facilities(), errors=errors)


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


@bp.route("/<uuid:employee_id>/delete", methods=["GET", "POST"])
def delete(employee_id):
    employee = get_employee(employee_id)
    if request.method == "GET":
        state = "active" if employee.is_active else "inactive"
        return render_template(
            "confirm.html",
            title="Delete employee",
            message=(
                f"Delete {employee.full_name} ({employee.employee_code}), who is {state}? "
                "This permanently deletes this employee and their related HR records, including employment, "
                "postings, appointments, and other history. Shared master records such as facilities and "
                "designations are kept. This cannot be undone."
            ),
            action=url_for("employees.delete", employee_id=employee.id),
            confirm_label="Delete employee",
            cancel_url=url_for("employees.view_record", employee_id=employee.id),
            version=employee.version,
        )
    version = request.form.get("version", type=int)
    if version is None:
        abort(400)
    try:
        code = delete_employee(g.actor_id, employee.id, version)
    except StaleEditError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("employees.view_record", employee_id=employee.id))
    except FieldError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("employees.view_record", employee_id=employee.id))
    except HTTPException:
        raise
    except Exception:
        db.session.rollback()
        flash("This employee could not be deleted. Nothing was removed.", "danger")
        return redirect(url_for("employees.view_record", employee_id=employee.id))
    flash(f"Employee {code} and their related HR records were deleted.", "success")
    return redirect(url_for("employees.index"))


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


def _selected_employees() -> list[tuple[str, str]]:
    chosen = request.form.getlist("selected")
    return [(item, request.form.get(f"version_{item}", "")) for item in chosen]


def _register_return_args() -> dict:
    args = {}
    for key in REGISTER_FIELDS:
        value = (request.form.get(key) or "").strip()
        if value:
            args[key] = value[:100]
    return args


def _download(payload: bytes, filename: str, mimetype: str):
    buffer = io.BytesIO(payload)
    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=filename)


def _active_facilities():
    return db.session.scalars(select(Facility).where(Facility.is_active.is_(True)).order_by(Facility.name)).all()


def _prepare_employee_form(form, employee):
    ensure_system_fields()
    selected = employee.facility_id if employee is not None else None
    if request.method == "POST":
        selected = request.form.get("facility_id") or selected
    form.facility_id.choices = active_choices(Facility, selected=selected, label=facility_label) or [
        ("", "Add a facility first")
    ]
    form.gender.choices = [("", "Select")] + list(GENDERS)
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
