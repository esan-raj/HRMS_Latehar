from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from app.choices import active_choices, all_choices, facility_label
from app.directory import employee_directory
from app.domain import FieldError, StaleEditError
from app.extensions import db
from app.forms import AddressForm, EmploymentForm, PersonalForm
from app.hr import create_employee, save_addresses, save_employment, set_employee_active, update_employee
from app.models import Address, Department, Designation, EmploymentDetail, EmploymentType, Facility
from app.web import apply_field_error, get_employee, render_employee

bp = Blueprint("employees", __name__, url_prefix="/employees")


@bp.route("/")
def index():
    page, filters, sort, direction = employee_directory(request.args)
    return render_template(
        "employees/list.html",
        page=page,
        filters=filters,
        sort=sort,
        direction=direction,
        query=filters.get("q", ""),
        status=filters.get("status", ""),
        designation_id=filters.get("designation_id", ""),
        facility_id=filters.get("facility_id", ""),
        employment_type_id=filters.get("employment_type_id", ""),
        designations=all_choices(Designation, blank="All designations"),
        facilities=all_choices(Facility, label=facility_label, blank="All facilities"),
        employment_types=all_choices(EmploymentType, blank="All employment types"),
    )


@bp.route("/new", methods=["GET", "POST"])
def create():
    form = PersonalForm()
    if form.validate_on_submit():
        try:
            employee = create_employee(g.actor_id, form.values())
        except FieldError as exc:
            apply_field_error(form, exc)
            return render_template("employees/form.html", form=form), 400
        flash("Employee created.", "success")
        return redirect(url_for("employees.personal", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return render_template("employees/form.html", form=form), status


@bp.route("/<uuid:employee_id>")
def profile(employee_id):
    get_employee(employee_id)
    return redirect(url_for("employees.personal", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/personal", methods=["GET", "POST"])
def personal(employee_id):
    employee = get_employee(employee_id)
    form = PersonalForm(obj=employee) if request.method == "GET" else PersonalForm()
    if form.validate_on_submit():
        try:
            _employee, changed = update_employee(g.actor_id, employee.id, form.version.data, form.values())
        except FieldError as exc:
            apply_field_error(form, exc)
            return render_employee("employees/personal.html", employee, "personal", form=form, status=400)
        except StaleEditError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            form = PersonalForm(obj=employee)
            flash(exc.message, "warning")
            return render_employee("employees/personal.html", employee, "personal", form=form, status=409)
        flash("Personal details saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.personal", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return render_employee("employees/personal.html", employee, "personal", form=form, status=status)


@bp.route("/<uuid:employee_id>/address", methods=["GET", "POST"])
def address(employee_id):
    employee = get_employee(employee_id)
    form = AddressForm()
    rows = _addresses(employee.id)
    if request.method == "GET":
        _bind_address(form, rows)
    if form.validate_on_submit():
        try:
            changed = save_addresses(
                g.actor_id,
                employee.id,
                form.group("present"),
                form.present_version.data,
                form.group("permanent"),
                form.permanent_version.data,
            )
        except FieldError as exc:
            apply_field_error(form, exc)
            return render_employee("employees/address.html", employee, "address", form=form, status=400)
        except StaleEditError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            form = AddressForm()
            _bind_address(form, _addresses(employee.id))
            flash(exc.message, "warning")
            return render_employee("employees/address.html", employee, "address", form=form, status=409)
        flash("Address saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.address", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return render_employee("employees/address.html", employee, "address", form=form, status=status)


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
            return render_employee("employees/employment.html", employee, "employment", form=form, status=400)
        except StaleEditError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            detail = _employment(employee.id)
            form = EmploymentForm()
            _prepare_employment(form, detail, True)
            flash(exc.message, "warning")
            return render_employee("employees/employment.html", employee, "employment", form=form, status=409)
        flash("Employment details saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.employment", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return render_employee("employees/employment.html", employee, "employment", form=form, status=status)


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


def _addresses(employee_id):
    rows = db.session.scalars(select(Address).where(Address.employee_id == employee_id)).all()
    return {row.address_type: row for row in rows}


def _bind_address(form, rows):
    for prefix in ("present", "permanent"):
        row = rows.get(prefix)
        if row is None:
            getattr(form, f"{prefix}_version").data = 0
            continue
        getattr(form, f"{prefix}_line1").data = row.line1
        getattr(form, f"{prefix}_line2").data = row.line2
        getattr(form, f"{prefix}_district").data = row.district
        getattr(form, f"{prefix}_state").data = row.state
        getattr(form, f"{prefix}_pin").data = row.pin_code
        getattr(form, f"{prefix}_version").data = row.version


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
