from datetime import date

from flask import flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from app.choices import active_choices, facility_label
from app.domain import FieldError, StaleEditError
from app.employee_routes import bp
from app.extensions import db
from app.forms import (
    AllegationForm,
    AppointmentForm,
    DacpForm,
    EducationForm,
    LeaveForm,
    PostingForm,
    PromotionForm,
    TrainingForm,
)
from app.hr import (
    POSTING_FIELDS,
    create_child,
    delete_child,
    save_posting,
    update_child,
)
from app.models import (
    Allegation,
    DacpDetail,
    Designation,
    EducationRecord,
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
from app.web import apply_field_error, get_employee, owned_record, render_employee

EDUCATION_FIELDS = ("qualification_id", "specialization", "institution", "passing_year")
TRAINING_FIELDS = ("training_type_id", "title", "provider", "start_date", "end_date", "remarks")
APPOINTMENT_FIELDS = (
    "appointment_reference",
    "appointment_date",
    "joining_date",
    "designation_id",
    "employment_type_id",
)
PROMOTION_FIELDS = ("previous_designation_id", "new_designation_id", "effective_date", "order_reference", "remarks")
ALLEGATION_FIELDS = ("reference_no", "description", "recorded_date", "status", "resolution_date", "remarks")
LEAVE_FIELDS = ("leave_type_id", "start_date", "end_date", "status", "remarks")
DACP_FIELDS = ("stage", "effective_date", "order_reference", "remarks")


def show(value):
    if value in (None, ""):
        return "—"
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def _prepare_education(form, record, bind):
    form.qualification_id.choices = active_choices(Qualification, selected=record.qualification_id if record else None)
    if bind and record:
        form.qualification_id.data = str(record.qualification_id)
        form.specialization.data = record.specialization
        form.institution.data = record.institution
        form.passing_year.data = record.passing_year
        form.version.data = record.version


def _prepare_training(form, record, bind):
    form.training_type_id.choices = active_choices(
        TrainingType, selected=record.training_type_id if record else None, blank="None"
    )
    if bind and record:
        form.training_type_id.data = str(record.training_type_id) if record.training_type_id else ""
        form.title.data = record.title
        form.provider.data = record.provider
        form.start_date.data = record.start_date
        form.end_date.data = record.end_date
        form.remarks.data = record.remarks
        form.version.data = record.version


def _prepare_appointment(form, record, bind):
    form.designation_id.choices = active_choices(Designation, selected=record.designation_id if record else None)
    form.employment_type_id.choices = active_choices(
        EmploymentType, selected=record.employment_type_id if record else None
    )
    if bind and record:
        form.appointment_reference.data = record.appointment_reference
        form.appointment_date.data = record.appointment_date
        form.joining_date.data = record.joining_date
        form.designation_id.data = str(record.designation_id)
        form.employment_type_id.data = str(record.employment_type_id)
        form.version.data = record.version


def _prepare_promotion(form, record, bind):
    form.previous_designation_id.choices = active_choices(
        Designation, selected=record.previous_designation_id if record else None
    )
    form.new_designation_id.choices = active_choices(Designation, selected=record.new_designation_id if record else None)
    if bind and record:
        form.previous_designation_id.data = str(record.previous_designation_id)
        form.new_designation_id.data = str(record.new_designation_id)
        form.effective_date.data = record.effective_date
        form.order_reference.data = record.order_reference
        form.remarks.data = record.remarks
        form.version.data = record.version


def _prepare_allegation(form, record, bind):
    if bind and record:
        form.reference_no.data = record.reference_no
        form.description.data = record.description
        form.recorded_date.data = record.recorded_date
        form.status.data = record.status
        form.resolution_date.data = record.resolution_date
        form.remarks.data = record.remarks
        form.version.data = record.version


def _prepare_leave(form, record, bind):
    form.leave_type_id.choices = active_choices(LeaveType, selected=record.leave_type_id if record else None)
    if bind and record:
        form.leave_type_id.data = str(record.leave_type_id)
        form.start_date.data = record.start_date
        form.end_date.data = record.end_date
        form.status.data = record.status
        form.remarks.data = record.remarks
        form.version.data = record.version


def _prepare_dacp(form, record, bind):
    if bind and record:
        form.stage.data = record.stage
        form.effective_date.data = record.effective_date
        form.order_reference.data = record.order_reference
        form.remarks.data = record.remarks
        form.version.data = record.version


def _prepare_posting(form, record, bind):
    form.facility_id.choices = active_choices(
        Facility, selected=record.facility_id if record else None, label=facility_label
    )
    form.designation_id.choices = active_choices(Designation, selected=record.designation_id if record else None)
    if bind and record:
        form.facility_id.data = str(record.facility_id)
        form.designation_id.data = str(record.designation_id)
        form.start_date.data = record.start_date
        form.end_date.data = record.end_date
        form.order_reference.data = record.order_reference
        form.remarks.data = record.remarks
        form.version.data = record.version


COLLECTIONS = (
    {
        "key": "education",
        "title": "Education",
        "empty": "No education records yet.",
        "model": EducationRecord,
        "form": EducationForm,
        "entity": "education",
        "fields": EDUCATION_FIELDS,
        "order": EducationRecord.passing_year.desc(),
        "prepare": _prepare_education,
        "columns": (
            ("Qualification", lambda row: row.qualification.name),
            ("Specialization", lambda row: show(row.specialization)),
            ("Institution", lambda row: row.institution),
            ("Passing year", lambda row: row.passing_year),
        ),
    },
    {
        "key": "training",
        "title": "Training History",
        "empty": "No training records yet.",
        "model": TrainingRecord,
        "form": TrainingForm,
        "entity": "training",
        "fields": TRAINING_FIELDS,
        "order": TrainingRecord.start_date.desc(),
        "prepare": _prepare_training,
        "columns": (
            ("Title", lambda row: row.title),
            ("Type", lambda row: row.training_type.name if row.training_type else "—"),
            ("Provider", lambda row: row.provider),
            ("Start", lambda row: show(row.start_date)),
            ("End", lambda row: show(row.end_date)),
        ),
    },
    {
        "key": "appointments",
        "title": "Job Appointments",
        "empty": "No job appointments yet.",
        "model": JobAppointment,
        "form": AppointmentForm,
        "entity": "job_appointment",
        "fields": APPOINTMENT_FIELDS,
        "order": JobAppointment.appointment_date.desc(),
        "prepare": _prepare_appointment,
        "columns": (
            ("Reference", lambda row: row.appointment_reference),
            ("Appointment", lambda row: show(row.appointment_date)),
            ("Joining", lambda row: show(row.joining_date)),
            ("Designation", lambda row: row.designation.name),
            ("Employment type", lambda row: row.employment_type.name),
        ),
    },
    {
        "key": "promotions",
        "title": "Promotions",
        "empty": "No promotions yet.",
        "model": Promotion,
        "form": PromotionForm,
        "entity": "promotion",
        "fields": PROMOTION_FIELDS,
        "order": Promotion.effective_date.desc(),
        "prepare": _prepare_promotion,
        "columns": (
            ("From", lambda row: row.previous_designation.name),
            ("To", lambda row: row.new_designation.name),
            ("Effective", lambda row: show(row.effective_date)),
            ("Order", lambda row: row.order_reference),
        ),
    },
    {
        "key": "allegations",
        "title": "Allegations Status",
        "empty": "No allegation records yet.",
        "model": Allegation,
        "form": AllegationForm,
        "entity": "allegation",
        "fields": ALLEGATION_FIELDS,
        "order": Allegation.recorded_date.desc(),
        "prepare": _prepare_allegation,
        "columns": (
            ("Reference", lambda row: row.reference_no),
            ("Recorded", lambda row: show(row.recorded_date)),
            ("Status", lambda row: row.status.replace("_", " ").title()),
            ("Resolution", lambda row: show(row.resolution_date)),
        ),
    },
    {
        "key": "leave",
        "title": "Leave Details",
        "empty": "No leave records yet.",
        "model": LeaveRecord,
        "form": LeaveForm,
        "entity": "leave",
        "fields": LEAVE_FIELDS,
        "order": LeaveRecord.start_date.desc(),
        "prepare": _prepare_leave,
        "columns": (
            ("Type", lambda row: row.leave_type.name),
            ("Start", lambda row: show(row.start_date)),
            ("End", lambda row: show(row.end_date)),
            ("Status", lambda row: row.status.replace("_", " ").title()),
        ),
    },
    {
        "key": "dacp",
        "title": "DACP Details",
        "empty": "No DACP records yet.",
        "model": DacpDetail,
        "form": DacpForm,
        "entity": "dacp",
        "fields": DACP_FIELDS,
        "order": DacpDetail.effective_date.desc(),
        "prepare": _prepare_dacp,
        "columns": (
            ("Stage", lambda row: row.stage),
            ("Effective", lambda row: show(row.effective_date)),
            ("Order", lambda row: row.order_reference),
        ),
    },
)


def _rows(spec, employee_id):
    return db.session.scalars(
        select(spec["model"]).where(spec["model"].employee_id == employee_id).order_by(spec["order"], spec["model"].id)
    ).all()


def _render_collection(employee, spec, form, status=200, editing=None):
    return render_employee(
        "employees/collection.html",
        employee,
        spec["key"],
        status=status,
        spec=spec,
        form=form,
        rows=_rows(spec, employee.id),
        editing=editing,
    )


def _register_collection(spec):
    key = spec["key"]

    def collection(employee_id, spec=spec):
        employee = get_employee(employee_id)
        if key == "appointments" and request.method == "GET":
            return redirect(_section_url(key, employee))
        form = spec["form"]()
        spec["prepare"](form, None, False)
        if form.validate_on_submit():
            try:
                create_child(g.actor_id, employee.id, spec["model"], spec["entity"], spec["fields"], form.values())
            except FieldError as exc:
                apply_field_error(form, exc)
                return _show_saved_collection(employee, spec, form, exc.status)
            flash("Job appointment saved." if key == "appointments" else f"{spec['title']} record saved.", "success")
            return redirect(_section_url(key, employee))
        status = 400 if request.method == "POST" else 200
        return _show_saved_collection(employee, spec, form, status)

    def edit(employee_id, record_id, spec=spec):
        employee = get_employee(employee_id)
        record = owned_record(spec["model"], employee.id, record_id)
        form = spec["form"]()
        spec["prepare"](form, record, request.method == "GET")
        if form.validate_on_submit():
            try:
                _row, changed = update_child(
                    g.actor_id,
                    employee.id,
                    spec["model"],
                    record.id,
                    spec["entity"],
                    spec["fields"],
                    form.version.data,
                    form.values(),
                )
            except FieldError as exc:
                apply_field_error(form, exc)
                return _show_saved_collection(employee, spec, form, exc.status, editing=record)
            except StaleEditError as exc:
                db.session.rollback()
                employee = get_employee(employee_id)
                record = owned_record(spec["model"], employee.id, record_id)
                form = spec["form"]()
                spec["prepare"](form, record, True)
                flash(exc.message, "warning")
                return _show_saved_collection(employee, spec, form, 409, editing=record)
            flash(f"{spec['title']} record saved." if changed else "No changes to save.", "success" if changed else "info")
            return redirect(_section_url(key, employee))
        status = 400 if request.method == "POST" else 200
        return _show_saved_collection(employee, spec, form, status, editing=record)

    def delete(employee_id, record_id, spec=spec):
        employee = get_employee(employee_id)
        record = owned_record(spec["model"], employee.id, record_id)
        if request.method == "GET":
            return render_template(
                "confirm.html",
                title=f"Delete {spec['title'].lower()} record",
                message="Delete this record? This removes the history entry.",
                action=url_for(f"employees.{key}_delete", employee_id=employee.id, record_id=record.id),
                confirm_label="Delete",
                cancel_url=_section_url(key, employee),
                version=record.version,
            )
        version = request.form.get("version", type=int)
        if version is None:
            from flask import abort

            abort(400)
        try:
            delete_child(
                g.actor_id, employee.id, spec["model"], record.id, spec["entity"], spec["fields"], version
            )
        except StaleEditError as exc:
            flash(exc.message, "warning")
            return redirect(_section_url(key, employee))
        flash("Record deleted.", "success")
        return redirect(_section_url(key, employee))

    collection.__name__ = f"{key}_list"
    edit.__name__ = f"{key}_edit"
    delete.__name__ = f"{key}_delete"
    bp.add_url_rule(f"/<uuid:employee_id>/{key}", view_func=collection, methods=["GET", "POST"], endpoint=f"{key}_list")
    bp.add_url_rule(
        f"/<uuid:employee_id>/{key}/<uuid:record_id>/edit",
        view_func=edit,
        methods=["GET", "POST"],
        endpoint=f"{key}_edit",
    )
    bp.add_url_rule(
        f"/<uuid:employee_id>/{key}/<uuid:record_id>/delete",
        view_func=delete,
        methods=["GET", "POST"],
        endpoint=f"{key}_delete",
    )


for _spec in COLLECTIONS:
    if _spec["key"] == "appointments":
        _register_collection(_spec)


def appointment_context(employee, form=None, editing=None):
    spec = next(item for item in COLLECTIONS if item["key"] == "appointments")
    if form is None:
        form = spec["form"]()
        spec["prepare"](form, None, False)
    return {
        "appointment_form": form,
        "appointment_rows": _rows(spec, employee.id),
        "appointment_spec": spec,
        "appointment_editing": editing,
    }


def _section_url(key, employee):
    if key == "appointments":
        return url_for("employees.employment", employee_id=employee.id)
    return url_for(f"employees.{key}_list", employee_id=employee.id)


def _show_saved_collection(employee, spec, form, status=200, editing=None):
    if spec["key"] != "appointments":
        return _render_collection(employee, spec, form, status, editing)
    from app.employee_routes import EmploymentForm, _employment, _prepare_employment, _render_employment

    detail = _employment(employee.id)
    employment_form = EmploymentForm()
    _prepare_employment(employment_form, detail, True)
    return _render_employment(employee, employment_form, status, form, editing)


@bp.route("/<uuid:employee_id>/postings", methods=["GET", "POST"])
def postings(employee_id):
    from app.web import current_posting

    employee = get_employee(employee_id)
    current = current_posting(employee.id)
    form = PostingForm()
    _prepare_posting(form, current, request.method == "GET")
    if form.validate_on_submit():
        values = form.values()
        values["end_date"] = None
        try:
            _row, changed = save_posting(
                g.actor_id,
                employee.id,
                current.id if current is not None else None,
                form.version.data,
                values,
            )
        except FieldError as exc:
            apply_field_error(form, exc)
            return _render_postings(employee, form, exc.status)
        flash("Current posting saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.postings", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return _render_postings(employee, form, status)


@bp.route("/<uuid:employee_id>/postings/<uuid:record_id>/edit", methods=["GET", "POST"])
def postings_edit(employee_id, record_id):
    employee = get_employee(employee_id)
    record = owned_record(Posting, employee.id, record_id)
    form = PostingForm()
    _prepare_posting(form, record, request.method == "GET")
    if form.validate_on_submit():
        try:
            _row, changed = save_posting(g.actor_id, employee.id, record.id, form.version.data, form.values())
        except FieldError as exc:
            apply_field_error(form, exc)
            return _render_postings(employee, form, exc.status, editing=record)
        except StaleEditError as exc:
            db.session.rollback()
            employee = get_employee(employee_id)
            record = owned_record(Posting, employee.id, record_id)
            form = PostingForm()
            _prepare_posting(form, record, True)
            flash(exc.message, "warning")
            return _render_postings(employee, form, 409, editing=record)
        flash("Posting saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("employees.postings", employee_id=employee.id))
    status = 400 if request.method == "POST" else 200
    return _render_postings(employee, form, status, editing=record)


@bp.route("/<uuid:employee_id>/postings/<uuid:record_id>/delete", methods=["GET", "POST"])
def postings_delete(employee_id, record_id):
    employee = get_employee(employee_id)
    record = owned_record(Posting, employee.id, record_id)
    if request.method == "GET":
        return render_template(
            "confirm.html",
            title="Delete posting",
            message="Delete this posting? This removes the history entry.",
            action=url_for("employees.postings_delete", employee_id=employee.id, record_id=record.id),
            confirm_label="Delete",
            cancel_url=url_for("employees.postings", employee_id=employee.id),
            version=record.version,
        )
    version = request.form.get("version", type=int)
    if version is None:
        from flask import abort

        abort(400)
    try:
        delete_child(g.actor_id, employee.id, Posting, record.id, "posting", POSTING_FIELDS, version)
    except StaleEditError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("employees.postings", employee_id=employee.id))
    flash("Posting deleted.", "success")
    return redirect(url_for("employees.postings", employee_id=employee.id))


def _render_postings(employee, form, status=200, editing=None):
    return render_employee(
        "employees/postings.html",
        employee,
        "postings",
        status=status,
        form=form,
        editing=editing,
    )
