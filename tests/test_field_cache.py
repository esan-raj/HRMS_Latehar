"""Request-scoped field catalog and incomplete-id reuse."""

from datetime import date

from sqlalchemy import event, select

from app.extensions import db
from app.fielddefs import (
    _parse_value,
    create_custom_field,
    incomplete_employee_ids,
    missing_value_counts,
    ordered_fields,
    stored_value_count,
)
from app.models import Employee, EmployeeField
from app.persistence import new_id
from tests.conftest import ADMIN_ID
from tests.test_employees import _facility, create_employee
from tests.test_fields import _add_field, _field


def _capture(admin, path):
    statements = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(db.engine, "before_cursor_execute", before_cursor_execute)
    try:
        response = admin.get(path)
    finally:
        event.remove(db.engine, "before_cursor_execute", before_cursor_execute)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response, statements


def _plain(statement):
    lowered = statement.lower()
    return lowered.replace("main.", "").replace("hrms.", "")


def _table_selects(statements, table):
    needle = f"from {table}"
    return [statement for statement in statements if _plain(statement).startswith("select") and needle in _plain(statement)]


def _incomplete_id_selects(statements):
    found = []
    for statement in statements:
        lowered = _plain(statement)
        if "select employees.id" in lowered and "facility_id is null" in lowered:
            found.append(statement)
    return found


def _filter_list_statements(statements):
    found = []
    for statement in statements:
        lowered = statement.lower()
        if "designations" in lowered and "employment_types" in lowered and "facilities" in lowered:
            found.append(statement)
    return found


def test_register_reads_load_the_field_catalog_once(admin):
    admin.get("/employees/")
    paths = (
        "/employees/",
        "/employees/?q=a&sort=name",
        "/employees/?page=2&sort=code",
        "/employees/?incomplete=1",
        "/employees/new",
        "/fields/",
    )
    for path in paths:
        _response, statements = _capture(admin, path)
        assert len(_table_selects(statements, "employee_fields")) == 1, path
        assert len(_table_selects(statements, "field_catalog")) == 1, path


def test_register_counts_incomplete_rows_without_loading_ids(admin):
    admin.get("/employees/")
    for path in ("/employees/", "/employees/?q=a&sort=name", "/employees/?page=2&sort=code", "/employees/?incomplete=1"):
        _response, statements = _capture(admin, path)
        assert _incomplete_id_selects(statements) == [], path
        assert len(_filter_list_statements(statements)) == 1, path
        assert _table_selects(statements, "employee_field_options") == [], path
    for path in ("/employees/new", "/fields/"):
        _response, statements = _capture(admin, path)
        assert _incomplete_id_selects(statements) == [], path
    _response, statements = _capture(admin, "/")
    assert _incomplete_id_selects(statements) == []
    assert _table_selects(statements, "employee_fields") == []


def test_next_request_sees_field_configuration_changes(admin):
    admin.get("/employees/new")
    phone = _field("Mobile number")
    phone.help_text = "Changed after the previous request"
    db.session.commit()
    _add_field(admin, label="Night shift", field_type="yes_no", section="Additional")
    page = admin.get("/employees/new")
    body = page.get_data(as_text=True)
    assert "Night shift" in body
    assert "Changed after the previous request" in body


def test_field_write_invalidates_the_request_catalog(app, admin):
    admin.get("/fields/")
    with app.test_request_context("/fields/"):
        assert all(field.label != "Cache probe" for field in ordered_fields())
        create_custom_field(
            ADMIN_ID,
            {
                "label": "Cache probe",
                "help_text": None,
                "placeholder": None,
                "field_type": "integer",
                "required": True,
                "section": "Additional",
                "show_in_register": False,
            },
            [],
        )
        labels = [field.label for field in ordered_fields()]
    assert "Cache probe" in labels
    assert db.session.scalar(select(EmployeeField).where(EmployeeField.label == "Cache probe")) is not None


def test_employee_write_invalidates_incomplete_ids(app, admin):
    admin.get("/employees/")
    facility = _facility()
    with app.test_request_context("/employees/"):
        employee_id = new_id()
        db.session.add(
            Employee(
                id=employee_id,
                employee_code="EMP-9001",
                full_name="Blank Phone",
                date_of_birth=date(1990, 1, 1),
                gender="female",
                phone="",
                facility_id=facility.id,
                custom_data={},
                is_active=True,
                version=1,
            )
        )
        db.session.commit()
        assert employee_id in incomplete_employee_ids()
        row = db.session.get(Employee, employee_id)
        row.phone = "9867543210"
        db.session.commit()
        assert employee_id not in incomplete_employee_ids()


def test_required_zero_and_false_are_present(admin, app):
    employee = create_employee(admin)
    number = _add_field(admin, label="Room number", field_type="integer", required="y", section="Notes")
    flag = _add_field(admin, label="On call", field_type="yes_no", required="y", section="Notes")
    assert _parse_value(number, 0, None) == 0
    assert _parse_value(number, "0", None) == 0
    assert _parse_value(flag, False, None) == "no"
    assert _parse_value(flag, "no", None) == "no"
    employee_id = employee.id
    stored = db.session.get(Employee, employee_id)
    stored.custom_data = {str(number.id): 0, str(flag.id): False}
    db.session.commit()
    with app.test_request_context("/employees/?incomplete=1"):
        assert employee_id not in incomplete_employee_ids()
        stored = db.session.get(Employee, employee_id)
        stored.custom_data = {}
        db.session.commit()
        assert employee_id in incomplete_employee_ids()


def test_register_banner_and_sql_counts_keep_zero_and_false(admin):
    complete = create_employee(admin, full_name="Complete Person")
    number = _add_field(admin, label="Room number", field_type="integer", required="y", section="Notes")
    flag = _add_field(admin, label="On call", field_type="yes_no", required="y", section="Notes")
    stored = db.session.get(Employee, complete.id)
    stored.custom_data = {str(number.id): 0, str(flag.id): False}
    db.session.commit()
    facility = _facility()
    blank_id = new_id()
    db.session.add(
        Employee(
            id=blank_id,
            employee_code="EMP-9002",
            full_name="Blank Phone",
            date_of_birth=date(1990, 1, 1),
            gender="female",
            phone="",
            facility_id=facility.id,
            custom_data={str(number.id): 0, str(flag.id): "no"},
            is_active=True,
            version=1,
        )
    )
    db.session.commit()
    page = admin.get("/employees/")
    assert b"1 employees are missing a required value" in page.data
    assert b"Complete Person" in page.data
    filtered = admin.get("/employees/?incomplete=1")
    assert b"Blank Phone" in filtered.data
    assert b"Complete Person" not in filtered.data
    counts = missing_value_counts(ordered_fields())
    assert counts[number.id] == 0
    assert counts[flag.id] == 0
    assert stored_value_count(number) == 2
    assert stored_value_count(flag) == 2
    _response, statements = _capture(admin, "/fields/")
    employee_reads = [statement for statement in statements if " from employees" in _plain(statement)]
    assert len(employee_reads) == 1
    assert all("custom_data" not in statement.lower() or "count" in statement.lower() or "sum(" in statement.lower() for statement in statements)
