from datetime import date

from sqlalchemy import select

from app.extensions import db
from app.fielddefs import backfill_employee_facilities
from app.models import AuditEvent, Employee, EmployeeField, EmployeeFieldOption, Facility, Posting
from app.persistence import new_id
from tests.test_employees import create_employee, payload


def _field(label: str) -> EmployeeField:
    return db.session.scalar(select(EmployeeField).where(EmployeeField.label == label))


def _add_field(admin, **overrides):
    data = {
        "label": "Badge number",
        "help_text": "Printed on the card",
        "placeholder": "B-100",
        "field_type": "short_text",
        "section": "Additional",
        "version": "0",
    }
    data.update(overrides)
    response = admin.post("/fields/", data=data, follow_redirects=True)
    assert response.status_code == 200, response.get_data(as_text=True)
    return _field(data["label"])


def test_mandatory_fields_cannot_be_removed_or_made_optional(admin):
    page = admin.get("/fields/")
    assert b"Employee name" in page.data
    assert b"Facility" in page.data
    assert b"Mobile number" in page.data
    name = _field("Employee name")
    archived = admin.post(f"/fields/{name.id}/archive", follow_redirects=True)
    assert b"cannot be archived" in archived.data
    assert db.session.get(EmployeeField, name.id).is_archived is False
    removed = admin.post(
        f"/fields/{name.id}/delete",
        data={"version": str(name.version), "confirm_label": name.label},
        follow_redirects=True,
    )
    assert b"cannot be deleted" in removed.data
    assert db.session.get(EmployeeField, name.id) is not None
    edited = admin.post(
        f"/fields/{name.id}/edit",
        data={
            "label": "Employee name",
            "section": "Personal Details",
            "field_type": "long_text",
            "show_in_register": "y",
            "version": str(name.version),
        },
    )
    assert edited.status_code == 302
    name = db.session.get(EmployeeField, name.id)
    assert name.required is True
    assert name.field_type == "short_text"
    missing = admin.post(
        "/employees/new",
        data=payload(version="0", phone="", facility_id="", fields_version="1"),
    )
    assert missing.status_code == 400
    assert b"Mobile number" in missing.data or b"mobile" in missing.data.lower()


def test_custom_field_is_rendered_and_each_type_is_validated(admin):
    short = _add_field(admin, label="Badge number", field_type="short_text", placeholder="B-100")
    long = _add_field(admin, label="Narrative", field_type="long_text", section="Notes")
    whole = _add_field(admin, label="Room number", field_type="integer", section="Notes")
    money = _add_field(admin, label="Allowance", field_type="decimal", section="Notes")
    joined = _add_field(admin, label="Review date", field_type="date", section="Notes")
    choice = _add_field(admin, label="Shift", field_type="dropdown", section="Notes", options="Day\nNight")
    flag = _add_field(admin, label="On call", field_type="yes_no", section="Notes")
    page = admin.get("/employees/new")
    body = page.get_data(as_text=True)
    assert "Badge number" in body
    assert "Printed on the card" in body
    assert 'placeholder="B-100"' in body
    assert 'pattern="[+-]?[0-9]+"' in body
    assert 'maxlength="200"' in body
    assert 'max="2100-12-31"' in body
    employee = create_employee(admin, employee_code="EMP-TYPE", fields_version=str(_catalog_version()))
    values = {
        f"custom_{short.id}": "B" * 201,
        f"custom_{long.id}": "N" * 4001,
        f"custom_{whole.id}": "12.5",
        f"custom_{money.id}": "lots",
        f"custom_{joined.id}": "13-13-13",
        f"custom_{choice.id}": "not-an-option",
        f"custom_{flag.id}": "maybe",
    }
    rejected = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(employee_code="EMP-TYPE", version=str(employee.version), facility_id=str(employee.facility_id), fields_version=str(_catalog_version()), **values),
    )
    text = rejected.get_data(as_text=True)
    assert rejected.status_code == 400
    assert "200 characters" in text
    assert "4000 characters" in text
    assert "whole number" in text
    assert "decimal" in text
    assert "valid date" in text
    assert "option" in text
    assert "yes or no" in text
    assert "B" * 201 in text
    option = db.session.scalar(select(EmployeeFieldOption).where(EmployeeFieldOption.field_id == choice.id))
    accepted = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-TYPE",
            version=str(employee.version),
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{
                f"custom_{short.id}": "B-100",
                f"custom_{long.id}": "A longer note",
                f"custom_{whole.id}": "12",
                f"custom_{money.id}": "10.50",
                f"custom_{joined.id}": "2024-05-01",
                f"custom_{choice.id}": str(option.id),
                f"custom_{flag.id}": "yes",
            },
        ),
    )
    assert accepted.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(short.id)] == "B-100"
    assert employee.custom_data[str(whole.id)] == 12
    assert employee.custom_data[str(money.id)] == "10.5"
    assert employee.custom_data[str(flag.id)] == "yes"


def test_label_change_preserves_values(admin):
    field = _add_field(admin, label="Badge number")
    employee = create_employee(admin, employee_code="EMP-LBL", fields_version=str(_catalog_version()))
    saved = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-LBL",
            version="1",
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{field.id}": "B-22"},
        ),
    )
    assert saved.status_code == 302
    renamed = admin.post(
        f"/fields/{field.id}/edit",
        data={
            "label": "Identity badge",
            "help_text": "Printed on the card",
            "section": "Additional",
            "field_type": "short_text",
            "version": str(field.version),
        },
    )
    assert renamed.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(field.id)] == "B-22"
    page = admin.get(f"/employees/{employee.id}/personal")
    assert b"Identity badge" in page.data
    assert b"B-22" in page.data


def test_required_field_added_for_existing_employees(admin):
    employee = create_employee(admin, employee_code="EMP-REQ")
    added = _add_field(admin, label="Pension number", required="y", show_in_register="y")
    assert employee.custom_data in (None, {}) or str(added.id) not in (employee.custom_data or {})
    listing = admin.get("/employees?incomplete=1")
    assert b"EMP-REQ" in listing.data
    blocked = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-REQ",
            version=str(employee.version),
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
        ),
    )
    assert blocked.status_code == 400
    assert b"required" in blocked.data
    employee = db.session.get(Employee, employee.id)
    assert str(added.id) not in (employee.custom_data or {})
    saved = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-REQ",
            version=str(employee.version),
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{added.id}": "PN-1"},
        ),
    )
    assert saved.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(added.id)] == "PN-1"


def test_incompatible_type_change_keeps_values(admin):
    field = _add_field(admin, label="Badge number")
    employee = create_employee(admin, employee_code="EMP-TYP2", fields_version=str(_catalog_version()))
    admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-TYP2",
            version="1",
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{field.id}": "ABC"},
        ),
    )
    field = db.session.get(EmployeeField, field.id)
    rejected = admin.post(
        f"/fields/{field.id}/edit",
        data={
            "label": "Badge number",
            "section": "Additional",
            "field_type": "integer",
            "version": str(field.version),
        },
    )
    assert rejected.status_code == 400
    assert b"would not fit" in rejected.data
    assert db.session.get(EmployeeField, field.id).field_type == "short_text"
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(field.id)] == "ABC"


def test_dropdown_option_retirement_keeps_selection(admin):
    field = _add_field(admin, label="Shift", field_type="dropdown", options="Day\nNight")
    day = db.session.scalar(select(EmployeeFieldOption).where(EmployeeFieldOption.label == "Day"))
    employee = create_employee(admin, employee_code="EMP-OPT", fields_version=str(_catalog_version()))
    admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-OPT",
            version="1",
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{field.id}": str(day.id)},
        ),
    )
    renamed = admin.post(
        f"/fields/{field.id}/edit",
        data={
            "label": "Duty shift",
            "section": "Additional",
            "field_type": "dropdown",
            "version": str(field.version),
        },
    )
    assert renamed.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(field.id)] == str(day.id)
    admin.post(f"/fields/{field.id}/options/{day.id}/retire")
    page = admin.get(f"/employees/{employee.id}/personal")
    assert b"Day" in page.data
    assert b"retired" in page.data
    denied = admin.post(f"/fields/{field.id}/options/{day.id}/delete", follow_redirects=True)
    assert b"Retire it instead" in denied.data
    assert db.session.get(EmployeeFieldOption, day.id) is not None


def test_hiding_a_column_preserves_data(admin):
    field = _add_field(admin, label="Locker", show_in_register="y")
    employee = create_employee(admin, employee_code="EMP-COL", fields_version=str(_catalog_version()))
    admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-COL",
            version="1",
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{field.id}": "LOCKER-77"},
        ),
    )
    visible = admin.get("/employees")
    assert b"LOCKER-77" in visible.data
    field = db.session.get(EmployeeField, field.id)
    admin.post(
        f"/fields/{field.id}/edit",
        data={
            "label": "Locker",
            "section": "Additional",
            "field_type": "short_text",
            "version": str(field.version),
        },
    )
    hidden = admin.get("/employees")
    assert b"LOCKER-77" not in hidden.data
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(field.id)] == "LOCKER-77"


def test_archive_and_restore_preserve_values(admin):
    field = _add_field(admin, label="Radio call sign")
    employee = create_employee(admin, employee_code="EMP-ARC", fields_version=str(_catalog_version()))
    admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-ARC",
            version="1",
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{field.id}": "ALPHA-1"},
        ),
    )
    admin.post(f"/fields/{field.id}/archive")
    archived = admin.get(f"/employees/{employee.id}/personal")
    assert b"Radio call sign" not in archived.data
    employee = db.session.get(Employee, employee.id)
    assert employee.custom_data[str(field.id)] == "ALPHA-1"
    admin.post(f"/fields/{field.id}/restore")
    restored = admin.get(f"/employees/{employee.id}/personal")
    assert b"Radio call sign" in restored.data
    assert b"ALPHA-1" in restored.data


def test_permanent_delete_removes_only_that_field(admin):
    first = _add_field(admin, label="Locker")
    second = _add_field(admin, label="Radio call sign", section="Notes")
    employee = create_employee(admin, employee_code="EMP-DEL", fields_version=str(_catalog_version()))
    admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-DEL",
            version="1",
            facility_id=str(employee.facility_id),
            fields_version=str(_catalog_version()),
            **{f"custom_{first.id}": "LOCKER-1", f"custom_{second.id}": "ALPHA-9"},
        ),
    )
    confirm = admin.get(f"/fields/{first.id}/delete")
    assert b"Locker" in confirm.data
    assert b"1" in confirm.data
    removed = admin.post(
        f"/fields/{first.id}/delete",
        data={"version": str(first.version), "confirm_label": "Locker"},
    )
    assert removed.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert str(first.id) not in employee.custom_data
    assert employee.custom_data[str(second.id)] == "ALPHA-9"
    audit = db.session.scalar(
        select(AuditEvent).where(AuditEvent.entity_type == "employee_field", AuditEvent.action == "delete")
    )
    assert audit.changes["label"]["before"] == "Locker"
    assert audit.changes["affected_records"]["before"] == 1
    assert "password" not in audit.changes


def test_stale_field_configuration_does_not_save(admin):
    employee = create_employee(admin, employee_code="EMP-STALE", full_name="Original Name")
    page = admin.get(f"/employees/{employee.id}/personal")
    assert b'name="fields_version" value="1"' in page.data or b"fields_version" in page.data
    _add_field(admin, label="Pension number", required="y")
    stale = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            employee_code="EMP-STALE",
            full_name="Changed Name",
            version=str(employee.version),
            facility_id=str(employee.facility_id),
            fields_version="1",
        ),
    )
    assert stale.status_code == 409
    assert b"review the fields" in stale.data
    employee = db.session.get(Employee, employee.id)
    assert employee.full_name == "Original Name"


def test_existing_employee_rows_survive_backfill_without_invented_values(app):
    facility = Facility(
        id=new_id(),
        name="Kept Facility",
        code="KEEP-1",
        facility_type="PHC",
        district="Northview",
        is_active=True,
        version=1,
    )
    posted = Employee(
        id=new_id(),
        employee_code="EMP-OLD1",
        full_name="Existing Person",
        date_of_birth=date(1980, 1, 1),
        gender="female",
        phone=None,
        facility_id=None,
        custom_data={"keep": "yes"},
        is_active=True,
        version=1,
    )
    unposted = Employee(
        id=new_id(),
        employee_code="EMP-OLD2",
        full_name="No Posting",
        date_of_birth=date(1981, 1, 1),
        gender="male",
        phone="",
        facility_id=None,
        custom_data={},
        is_active=True,
        version=1,
    )
    db.session.add_all([facility, posted, unposted])
    db.session.flush()
    db.session.add(
        Posting(
            id=new_id(),
            employee_id=posted.id,
            facility_id=facility.id,
            designation_id=_designation(),
            start_date=date(2020, 1, 1),
            end_date=None,
            order_reference="OLD-1",
            version=1,
        )
    )
    db.session.commit()
    assert backfill_employee_facilities() == 1
    db.session.commit()
    posted = db.session.get(Employee, posted.id)
    unposted = db.session.get(Employee, unposted.id)
    assert posted.phone is None
    assert posted.facility_id == facility.id
    assert posted.custom_data["keep"] == "yes"
    assert unposted.phone == ""
    assert unposted.facility_id is None
    assert db.session.scalar(select(Employee).where(Employee.employee_code == "EMP-OLD1")) is not None


def _catalog_version() -> int:
    from app.models import FieldCatalog

    return db.session.get(FieldCatalog, 1).version


def _designation():
    from app.models import Designation

    row = db.session.scalar(select(Designation).limit(1))
    if row is not None:
        return row.id
    row = Designation(id=new_id(), name="Medical Officer", is_active=True, version=1)
    db.session.add(row)
    db.session.flush()
    return row.id
