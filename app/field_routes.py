from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from app.domain import FieldError, StaleEditError
from app.extensions import db
from app.fielddefs import (
    CUSTOM_TYPES,
    add_option,
    create_custom_field,
    delete_custom_field,
    delete_option,
    ensure_system_fields,
    missing_value_count,
    move_field,
    ordered_fields,
    retire_option,
    set_archived,
    split_options,
    stored_value_count,
    update_custom_field,
)
from app.forms import FieldDefinitionForm
from app.models import EmployeeField
from app.web import apply_field_error

bp = Blueprint("fields", __name__, url_prefix="/fields")


@bp.route("/", methods=["GET", "POST"])
def index():
    ensure_system_fields()
    form = FieldDefinitionForm()
    form.field_type.choices = CUSTOM_TYPES
    if form.validate_on_submit():
        try:
            options = split_options(form.options.data) if form.field_type.data == "dropdown" else []
            field = create_custom_field(g.actor_id, _values(form), options)
        except FieldError as exc:
            apply_field_error(form, exc)
            return _index(form, 400)
        count = missing_value_count(field) if field.required else 0
        if field.required:
            flash(f"Field added. {count} existing employees do not have this value yet.", "warning")
        else:
            flash("Field added.", "success")
        return redirect(url_for("fields.index"))
    status = 400 if request.method == "POST" else 200
    return _index(form, status)


@bp.route("/<uuid:field_id>/edit", methods=["GET", "POST"])
def edit(field_id):
    ensure_system_fields()
    field = _field(field_id)
    form = FieldDefinitionForm()
    form.field_type.choices = CUSTOM_TYPES
    if request.method == "GET":
        _fill(form, field)
    if form.validate_on_submit():
        try:
            options = split_options(form.options.data) if not field.is_system else []
            update_custom_field(g.actor_id, field.id, form.version.data, _values(form, field), options)
        except FieldError as exc:
            apply_field_error(form, exc)
            return _edit(field, form, 400)
        except StaleEditError as exc:
            db.session.rollback()
            field = _field(field_id)
            form = FieldDefinitionForm()
            form.field_type.choices = CUSTOM_TYPES
            _fill(form, field)
            flash(exc.message, "warning")
            return _edit(field, form, 409)
        flash("Field saved.", "success")
        return redirect(url_for("fields.edit", field_id=field.id))
    status = 400 if request.method == "POST" else 200
    return _edit(field, form, status)


@bp.route("/<uuid:field_id>/up", methods=["POST"])
def up(field_id):
    return _move(field_id, -1)


@bp.route("/<uuid:field_id>/down", methods=["POST"])
def down(field_id):
    return _move(field_id, 1)


@bp.route("/<uuid:field_id>/archive", methods=["POST"])
def archive(field_id):
    return _archive(field_id, True)


@bp.route("/<uuid:field_id>/restore", methods=["POST"])
def restore(field_id):
    return _archive(field_id, False)


@bp.route("/<uuid:field_id>/delete", methods=["GET", "POST"])
def delete(field_id):
    ensure_system_fields()
    field = _field(field_id)
    count = stored_value_count(field) if not field.is_system else 0
    if request.method == "GET":
        return render_template("fields/delete.html", field=field, count=count)
    if field.is_system:
        flash("Employee name, facility, and mobile number cannot be deleted.", "danger")
        return redirect(url_for("fields.index"))
    try:
        delete_custom_field(g.actor_id, field.id, request.form.get("version", type=int), request.form.get("confirm_label", ""))
    except FieldError as exc:
        flash(exc.message, "danger")
        return render_template("fields/delete.html", field=field, count=count), 400
    except StaleEditError as exc:
        flash(exc.message, "warning")
        return redirect(url_for("fields.edit", field_id=field.id))
    flash("Field deleted.", "success")
    return redirect(url_for("fields.index"))


@bp.route("/<uuid:field_id>/options", methods=["POST"])
def add_field_option(field_id):
    try:
        add_option(g.actor_id, field_id, request.form.get("option_label", ""))
    except FieldError as exc:
        flash(exc.message, "danger")
    else:
        flash("Option added.", "success")
    return redirect(url_for("fields.edit", field_id=field_id))


@bp.route("/<uuid:field_id>/options/<uuid:option_id>/retire", methods=["POST"])
def retire_field_option(field_id, option_id):
    retire_option(g.actor_id, field_id, option_id)
    flash("Option retired. Existing selections still show this label.", "success")
    return redirect(url_for("fields.edit", field_id=field_id))


@bp.route("/<uuid:field_id>/options/<uuid:option_id>/delete", methods=["POST"])
def delete_field_option(field_id, option_id):
    try:
        delete_option(g.actor_id, field_id, option_id)
    except FieldError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("fields.edit", field_id=field_id))
    flash("Option deleted.", "success")
    return redirect(url_for("fields.edit", field_id=field_id))


def _index(form, status):
    fields = ordered_fields()
    counts = {field.id: missing_value_count(field) for field in fields if field.required and not field.is_archived}
    body = render_template("fields/list.html", form=form, fields=fields, counts=counts)
    return body, status


def _edit(field, form, status):
    body = render_template("fields/form.html", form=form, field=field, count=stored_value_count(field))
    return body, status


def _move(field_id, direction: int):
    ensure_system_fields()
    _field(field_id)
    move_field(g.actor_id, field_id, direction)
    return redirect(url_for("fields.index"))


def _archive(field_id, archived: bool):
    ensure_system_fields()
    try:
        set_archived(g.actor_id, field_id, archived)
    except FieldError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("fields.index"))
    flash("Field archived." if archived else "Field restored.", "success")
    return redirect(url_for("fields.index"))


def _field(field_id) -> EmployeeField:
    field = db.session.get(EmployeeField, field_id)
    if field is None:
        abort(404)
    return field


def _values(form, existing: EmployeeField | None = None) -> dict:
    field_type = existing.field_type if existing is not None and existing.is_system else form.field_type.data
    required = True if existing is not None and existing.is_system else bool(form.required.data)
    return {
        "label": form.label.data,
        "help_text": form.help_text.data,
        "placeholder": form.placeholder.data,
        "field_type": field_type,
        "required": required,
        "section": form.section.data or "Personal Details",
        "show_in_register": bool(form.show_in_register.data),
    }


def _fill(form, field: EmployeeField) -> None:
    form.label.data = field.label
    form.help_text.data = field.help_text
    form.placeholder.data = field.placeholder
    form.field_type.data = field.field_type if field.field_type in dict(CUSTOM_TYPES) else "short_text"
    form.required.data = field.required
    form.section.data = field.section
    form.show_in_register.data = field.show_in_register
    form.version.data = field.version
