import io

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from app.audit import column_snapshot, record_audit
from app.domain import DomainError, FieldError, StaleEditError
from app.extensions import db
from app.forms import FacilityForm, MasterNameForm
from app.hr import (
    MASTER_MODELS,
    allocate_facility_code,
    delete_blocked_message,
    name_taken,
    next_facility_code,
    referenced_labels,
)
from app.master_transfer import import_masters, master_table, template_columns, template_csv
from app.sheet_pdf import table_pdf
from app.transfers import EXPORT_LIMIT, BulkFileError, workbook_bytes
from app.persistence import apply_versioned_update, commit_or_conflict, new_id, reload_locked

bp = Blueprint("masters", __name__, url_prefix="/masters")

SPECS = {
    "departments": {"title": "Departments", "singular": "department", "entity": "department", "facility": False},
    "designations": {"title": "Designations", "singular": "designation", "entity": "designation", "facility": False},
    "facilities": {"title": "Facilities", "singular": "facility", "entity": "facility", "facility": True},
    "employment-types": {
        "title": "Employment types",
        "singular": "employment type",
        "entity": "employment_type",
        "facility": False,
    },
}


def _spec(slug):
    spec = SPECS.get(slug)
    if spec is None:
        abort(404)
    spec = dict(spec)
    spec["slug"] = slug
    spec["model"] = MASTER_MODELS[slug]
    spec["fields"] = (
        ("name", "code", "facility_type", "district", "block", "is_active")
        if spec["facility"]
        else ("name", "is_active")
    )
    return spec


@bp.route("/<slug>", methods=["GET", "POST"])
def index(slug):
    spec = _spec(slug)
    form = FacilityForm() if spec["facility"] else MasterNameForm()
    if form.validate_on_submit():
        try:
            _create_master(spec, form)
        except FieldError as exc:
            field = getattr(form, exc.field, None)
            if field is None:
                flash(exc.message, "danger")
            else:
                field.errors.append(exc.message)
            return _render_list(spec, form), 400
        flash(f"{spec['singular'].capitalize()} saved.", "success")
        return redirect(url_for("masters.index", slug=slug))
    status = 400 if request.method == "POST" else 200
    return _render_list(spec, form), status


@bp.route("/<slug>/export/excel")
def export_xlsx(slug):
    spec = _spec(slug)
    headers, rows, truncated = master_table(spec)
    payload = workbook_bytes(headers, rows, truncated, spec["title"])
    return _download(payload, f"{slug}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/<slug>/export/pdf")
def export_pdf(slug):
    spec = _spec(slug)
    headers, rows, truncated = master_table(spec)
    note = f"This file contains the first {EXPORT_LIMIT} matching rows." if truncated else ""
    payload = table_pdf(headers, rows, spec["title"], note)
    return _download(payload, f"{slug}.pdf", "application/pdf")


@bp.route("/<slug>/bulk/template")
def bulk_template(slug):
    spec = _spec(slug)
    return _download(template_csv(spec), f"{slug}-template.csv", "text/csv; charset=utf-8")


@bp.route("/<slug>/bulk", methods=["GET", "POST"])
def bulk(slug):
    spec = _spec(slug)
    errors = []
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            errors = ["Choose a CSV file."]
        elif not upload.filename.lower().endswith(".csv"):
            errors = ["Upload a CSV file made from the sample template."]
        else:
            try:
                result = import_masters(g.actor_id, spec, upload.read())
            except BulkFileError as exc:
                errors = [exc.message]
            else:
                if result.errors:
                    errors = result.errors
                else:
                    label = spec["singular"] if result.created == 1 else spec["title"].lower()
                    flash(f"Added {result.created} {label}.", "success")
                    return redirect(url_for("masters.index", slug=slug))
        return render_template("masters/bulk.html", spec=spec, columns=template_columns(spec), errors=errors), 400
    return render_template("masters/bulk.html", spec=spec, columns=template_columns(spec), errors=errors)


@bp.route("/<slug>/<uuid:record_id>/edit", methods=["GET", "POST"])
def edit(slug, record_id):
    spec = _spec(slug)
    record = db.session.get(spec["model"], record_id)
    if record is None:
        abort(404)
    form = FacilityForm() if spec["facility"] else MasterNameForm()
    if request.method == "GET":
        _bind_master(form, record, spec["facility"])
    if form.validate_on_submit():
        try:
            changed = _update_master(spec, record.id, form.version.data, form.values())
        except FieldError as exc:
            field = getattr(form, exc.field, None)
            if field is None:
                flash(exc.message, "danger")
            else:
                field.errors.append(exc.message)
            return render_template("masters/form.html", spec=spec, form=form, record=record), 400
        except StaleEditError as exc:
            db.session.rollback()
            record = db.session.get(spec["model"], record_id)
            form = FacilityForm() if spec["facility"] else MasterNameForm()
            _bind_master(form, record, spec["facility"])
            flash(exc.message, "warning")
            return render_template("masters/form.html", spec=spec, form=form, record=record), 409
        flash("Changes saved." if changed else "No changes to save.", "success" if changed else "info")
        return redirect(url_for("masters.index", slug=slug))
    status = 400 if request.method == "POST" else 200
    return render_template("masters/form.html", spec=spec, form=form, record=record), status


@bp.route("/<slug>/<uuid:record_id>/deactivate", methods=["GET", "POST"])
def deactivate(slug, record_id):
    return _activation(slug, record_id, False)


@bp.route("/<slug>/<uuid:record_id>/activate", methods=["GET", "POST"])
def activate(slug, record_id):
    return _activation(slug, record_id, True)


@bp.route("/<slug>/<uuid:record_id>/delete", methods=["GET", "POST"])
def delete(slug, record_id):
    spec = _spec(slug)
    record = db.session.get(spec["model"], record_id)
    if record is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "confirm.html",
            title=f"Delete {spec['singular']}",
            message=f"Delete {record.name}? If it is used by employee records, delete will be refused.",
            action=url_for("masters.delete", slug=slug, record_id=record.id),
            confirm_label="Delete",
            cancel_url=url_for("masters.index", slug=slug),
            version=record.version,
        )
    version = request.form.get("version", type=int)
    if version is None:
        abort(400)
    try:
        _delete_master(spec, record.id, version)
    except DomainError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("masters.index", slug=slug))
    except StaleEditError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("masters.index", slug=slug))
    except FieldError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("masters.index", slug=slug))
    flash(f"{spec['singular'].capitalize()} deleted.", "success")
    return redirect(url_for("masters.index", slug=slug))


def _download(payload: bytes, filename: str, mimetype: str):
    buffer = io.BytesIO(payload)
    buffer.seek(0)
    return send_file(buffer, mimetype=mimetype, as_attachment=True, download_name=filename)


def _render_list(spec, form):
    rows = db.session.scalars(select(spec["model"]).order_by(spec["model"].name)).all()
    next_code = next_facility_code() if spec["facility"] else None
    return render_template("masters/list.html", spec=spec, form=form, rows=rows, next_code=next_code)


def _create_master(spec, form):
    values = form.values()
    if spec["facility"]:
        values["code"] = allocate_facility_code()
    _reject_duplicates(spec, values, None)
    row = spec["model"](id=new_id(), version=1, is_active=True, **values)
    db.session.add(row)
    record_audit(g.actor_id, spec["entity"], row.id, "create", {}, column_snapshot(row, spec["fields"]))
    commit_or_conflict("code" if spec["facility"] else "name")


def _update_master(spec, record_id, version, values) -> bool:
    _reject_duplicates(spec, values, record_id)
    row = reload_locked(spec["model"], record_id)
    before, after, changed = apply_versioned_update(row, version, values, spec["fields"])
    if not changed:
        db.session.rollback()
        return False
    record_audit(g.actor_id, spec["entity"], row.id, "update", before, after)
    commit_or_conflict("code" if spec["facility"] else "name")
    return True


def _delete_master(spec, record_id, version):
    labels = referenced_labels(spec["slug"], record_id)
    if labels:
        raise DomainError(delete_blocked_message(spec["singular"], labels))
    row = reload_locked(spec["model"], record_id)
    if row.version != version:
        db.session.rollback()
        raise StaleEditError()
    before = column_snapshot(row, spec["fields"])
    record_audit(g.actor_id, spec["entity"], row.id, "delete", before, {})
    db.session.delete(row)
    commit_or_conflict("name")


def _activation(slug, record_id, active: bool):
    spec = _spec(slug)
    record = db.session.get(spec["model"], record_id)
    if record is None:
        abort(404)
    action = "Activate" if active else "Deactivate"
    if request.method == "GET":
        return render_template(
            "confirm.html",
            title=f"{action} {spec['singular']}",
            message=f"{action} {record.name}? Inactive records stay on existing history but cannot be chosen for new records.",
            action=url_for(request.endpoint, slug=slug, record_id=record.id),
            confirm_label=action,
            cancel_url=url_for("masters.index", slug=slug),
            version=record.version,
        )
    version = request.form.get("version", type=int)
    if version is None:
        abort(400)
    try:
        changed = _set_active(spec, record.id, version, active)
    except StaleEditError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("masters.index", slug=slug))
    flash(
        f"{spec['singular'].capitalize()} {action.lower()}d." if changed else f"It is already {action.lower()}d.",
        "success" if changed else "info",
    )
    return redirect(url_for("masters.index", slug=slug))


def _set_active(spec, record_id, version, active: bool) -> bool:
    row = reload_locked(spec["model"], record_id)
    if row.version != version:
        db.session.rollback()
        raise StaleEditError()
    if row.is_active == active:
        db.session.rollback()
        return False
    before, after, _changed = apply_versioned_update(row, version, {"is_active": active}, spec["fields"])
    record_audit(
        g.actor_id,
        spec["entity"],
        row.id,
        "reactivate" if active else "deactivate",
        before,
        after,
    )
    commit_or_conflict("name")
    return True


def _reject_duplicates(spec, values, exclude_id):
    if spec["facility"]:
        return
    if name_taken(spec["model"], values["name"], exclude_id):
        db.session.rollback()
        raise FieldError("name", "A record with this name already exists.")


def _bind_master(form, record, facility: bool) -> None:
    form.name.data = record.name
    form.version.data = record.version
    if not facility:
        return
    form.facility_type.data = record.facility_type
    form.district.data = record.district
    form.block.data = record.block
