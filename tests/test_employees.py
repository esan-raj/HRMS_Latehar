import uuid
from datetime import date

from sqlalchemy import event, func, select

from app.extensions import db
from app.models import (
    AuditEvent,
    Department,
    Designation,
    Employee,
    EmploymentDetail,
    EmploymentType,
    Facility,
    Posting,
)
from app.persistence import new_id


def _facility():
    existing = db.session.scalar(select(Facility).limit(1))
    if existing is not None:
        return existing
    row = Facility(
        id=new_id(),
        name="Northview PHC",
        code="PHC-TEST",
        facility_type="PHC",
        district="Northview",
        is_active=True,
        version=1,
    )
    db.session.add(row)
    db.session.commit()
    return row


def payload(**overrides):
    data = {
        "employee_code": "EMP-2001",
        "full_name": "Asha Menon",
        "date_of_birth": "1990-01-15",
        "gender": "female",
        "phone": "9867543210",
        "email": "asha@example.com",
        "is_active": "y",
        "version": "1",
        "fields_version": "1",
    }
    data.update(overrides)
    return data


def create_employee(admin, **overrides):
    facility = _facility()
    data = payload(version="0", facility_id=str(facility.id), **overrides)
    response = admin.post("/employees/new", data=data)
    assert response.status_code == 302, response.get_data(as_text=True)
    employee_id = response.headers["Location"].rstrip("/").split("/")[-2]
    return db.session.get(Employee, uuid.UUID(employee_id))


def test_employee_form_rejects_weak_name_and_phone(admin):
    facility = _facility()
    page = admin.get("/employees/new")
    assert b'maxlength="10"' in page.data
    assert b'pattern="[6-9][0-9]{9}"' in page.data
    rejected = admin.post(
        "/employees/new",
        data=payload(
            version="0",
            facility_id=str(facility.id),
            full_name="1234",
            phone="+919867543210",
        ),
    )
    body = rejected.get_data(as_text=True)
    assert rejected.status_code == 400
    assert "includes letters" in body
    assert "country code" in body
    for fake in ("1234567890", "0000000000", "9876543210", "1111111111"):
        fake_response = admin.post(
            "/employees/new",
            data=payload(version="0", facility_id=str(facility.id), phone=fake, employee_code="EMP-FAKE"),
        )
        assert fake_response.status_code == 400
        assert "real mobile number" in fake_response.get_data(as_text=True)
    leading_zero = admin.post(
        "/employees/new",
        data=payload(version="0", facility_id=str(facility.id), phone="0986712345", employee_code="EMP-FAKE"),
    )
    spaced = admin.post(
        "/employees/new",
        data=payload(version="0", facility_id=str(facility.id), phone="9867 123450", employee_code="EMP-FAKE"),
    )
    assert leading_zero.status_code == 400
    assert "starting with 6, 7, 8, or 9" in leading_zero.get_data(as_text=True)
    assert spaced.status_code == 400
    assert "exactly 10 digits" in spaced.get_data(as_text=True)
    assert db.session.scalar(select(Employee).where(Employee.employee_code == "EMP-2001")) is None


def test_employee_crud_deactivation_and_phone_string(admin):
    employee = create_employee(admin)
    assert employee.phone == "9867543210"
    assert isinstance(employee.phone, str)
    page = admin.get(f"/employees/{employee.id}/personal")
    assert page.status_code == 200
    assert b"Asha Menon" in page.data

    updated = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(full_name="Asha K Menon", version=str(employee.version), facility_id=str(employee.facility_id)),
    )
    assert updated.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.full_name == "Asha K Menon"

    confirm = admin.get(f"/employees/{employee.id}/deactivate")
    assert confirm.status_code == 200
    assert employee.is_active is True
    deactivated = admin.post(f"/employees/{employee.id}/deactivate", data={"version": str(employee.version)})
    assert deactivated.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.is_active is False
    reactivated = admin.post(f"/employees/{employee.id}/reactivate", data={"version": str(employee.version)})
    assert reactivated.status_code == 302
    employee = db.session.get(Employee, employee.id)
    assert employee.is_active is True
    missing = admin.post(f"/employees/{employee.id}/delete")
    assert missing.status_code == 404


def test_generated_code_does_not_read_every_employee_code(admin, app):
    from sqlalchemy import event

    create_employee(admin, full_name="First Code")
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).lower())

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        with app.test_request_context():
            from app.hr import allocate_employee_code

            reserved = allocate_employee_code()
            db.session.rollback()
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert reserved == "EMP-0002"
    assert not any(
        statement.replace("main.", "").replace("hrms.", "").startswith("select employees.employee_code")
        and " where " not in statement
        for statement in statements
    )


def test_employee_code_is_generated_and_cannot_be_changed(admin):
    page = admin.get("/employees/new")
    assert b"EMP-0001" in page.data
    assert b'name="employee_code"' not in page.data
    first = create_employee(admin, full_name="First Person")
    second = create_employee(admin, full_name="Other Person", employee_code="EMP-9999")
    assert first.employee_code == "EMP-0001"
    assert second.employee_code == "EMP-0002"
    changed = admin.post(
        f"/employees/{first.id}/personal",
        data=payload(
            employee_code="EMP-9999",
            full_name="First Person",
            version=str(first.version),
            facility_id=str(first.facility_id),
        ),
    )
    assert changed.status_code == 302
    first = db.session.get(Employee, first.id)
    assert first.employee_code == "EMP-0001"


def test_stale_personal_edit_does_not_overwrite(admin):
    employee = create_employee(admin, full_name="First Name")
    first = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(full_name="Second Name", version="1", facility_id=str(employee.facility_id)),
    )
    assert first.status_code == 302
    stale = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(full_name="Third Name", version="1", facility_id=str(employee.facility_id)),
    )
    assert stale.status_code == 409
    assert b"latest values" in stale.data
    employee = db.session.get(Employee, employee.id)
    assert employee.full_name == "Second Name"


def test_audit_records_create_update_and_deactivate(admin, app):
    employee = create_employee(admin, employee_code="EMP-AUD1")
    created = db.session.scalar(select(AuditEvent).where(AuditEvent.action == "create", AuditEvent.entity_id == employee.id))
    assert created.actor_id == __import__("uuid").UUID(app.config["ADMIN_USER_ID"])
    assert created.entity_type == "employee"
    assert created.changes["employee_code"]["after"] == employee.employee_code
    assert created.changes["phone"]["after"] == "9867543210"
    assert "password" not in created.changes
    admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(employee_code="EMP-AUD1", full_name="Audited Name", version="1", facility_id=str(employee.facility_id)),
    )
    updated = db.session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == employee.id, AuditEvent.action == "update")
    ).all()
    assert updated[-1].changes["full_name"] == {"before": "Asha Menon", "after": "Audited Name"}
    employee = db.session.get(Employee, employee.id)
    admin.post(f"/employees/{employee.id}/deactivate", data={"version": str(employee.version)})
    deactivated = db.session.scalar(
        select(AuditEvent).where(AuditEvent.entity_id == employee.id, AuditEvent.action == "deactivate")
    )
    assert deactivated.changes["is_active"] == {"before": True, "after": False}


def test_profile_keeps_personal_employment_and_current_posting(admin):
    employee = create_employee(admin)
    page = admin.get(f"/employees/{employee.id}/personal")
    body = page.get_data(as_text=True)
    assert "Personal Details" in body
    assert "Employment" in body
    assert "Current Posting" in body
    for removed in ("Address", "Education", "Training History", "Promotions", "Allegations", "Leave Details", "DACP"):
        assert removed not in body
    assert admin.get(f"/employees/{employee.id}/education").status_code == 404
    assert admin.get(f"/employees/{employee.id}/address").status_code == 404
    assert admin.get("/masters/qualifications").status_code == 404
    assert admin.get("/masters/training-types").status_code == 404
    assert admin.get("/masters/leave-types").status_code == 404
    employment = admin.get(f"/employees/{employee.id}/employment")
    employment_body = employment.get_data(as_text=True)
    assert employment.status_code == 200
    assert "Employment details" in employment_body
    assert "Job appointments" in employment_body
    assert "Appointment reference" in employment_body


def test_referenced_master_cannot_be_deleted(admin):
    employee = create_employee(admin)
    department = Department(id=new_id(), name="Clinical Services", is_active=True, version=1)
    employment_type = EmploymentType(id=new_id(), name="Regular", is_active=True, version=1)
    db.session.add_all([department, employment_type])
    db.session.commit()
    saved = admin.post(
        f"/employees/{employee.id}/employment",
        data={
            "department_id": str(department.id),
            "employment_type_id": str(employment_type.id),
            "service_joining_date": "2016-01-01",
            "pay_level": "Level 10",
            "version": "0",
        },
    )
    assert saved.status_code == 302
    denied = admin.post(f"/masters/departments/{department.id}/delete", data={"version": "1"}, follow_redirects=True)
    assert b"Deactivate it instead" in denied.data
    assert db.session.get(Department, department.id) is not None
    deactivated = admin.post(f"/masters/departments/{department.id}/deactivate", data={"version": "1"})
    assert deactivated.status_code == 302
    department = db.session.get(Department, department.id)
    assert department.is_active is False
    detail = db.session.scalar(select(EmploymentDetail))
    assert detail.department.name == "Clinical Services"


def test_single_open_posting_and_date_integrity(admin):
    employee = create_employee(admin)
    facility = Facility(
        id=new_id(), name="Northview PHC", code="PHC-NORTH", facility_type="PHC", district="Northview", is_active=True, version=1
    )
    other = Facility(
        id=new_id(), name="Rivermark CHC", code="CHC-RIVER", facility_type="CHC", district="Rivermark", is_active=True, version=1
    )
    designation = Designation(id=new_id(), name="Medical Officer", is_active=True, version=1)
    db.session.add_all([facility, other, designation])
    db.session.commit()

    def posting(facility_id, start, end, version="0"):
        return {
            "facility_id": str(facility_id),
            "designation_id": str(designation.id),
            "start_date": start,
            "end_date": end,
            "order_reference": "ORD-1",
            "version": version,
        }

    first = admin.post(f"/employees/{employee.id}/postings", data=posting(facility.id, "2020-01-01", ""))
    assert first.status_code == 302
    page = admin.get(f"/employees/{employee.id}/postings")
    assert b"Current posting" in page.data
    assert b"Posting History" not in page.data
    assert b'name="end_date"' not in page.data
    current = db.session.scalar(select(Posting).where(Posting.end_date.is_(None)))
    updated = admin.post(
        f"/employees/{employee.id}/postings",
        data=posting(other.id, "2021-01-01", "2022-01-01", version=str(current.version)),
    )
    assert updated.status_code == 302
    open_rows = db.session.scalars(select(Posting).where(Posting.end_date.is_(None))).all()
    assert len(open_rows) == 1
    assert open_rows[0].facility_id == other.id
    assert open_rows[0].end_date is None
    assert open_rows[0].start_date == date(2021, 1, 1)


def test_search_filter_sort_and_pagination(admin):
    officer = Designation(id=new_id(), name="Medical Officer", is_active=True, version=1)
    facility = Facility(
        id=new_id(), name="Northview PHC", code="PHC-N", facility_type="PHC", district="Northview", is_active=True, version=1
    )
    db.session.add_all([officer, facility])
    db.session.commit()
    for number in range(1, 26):
        create_employee(
            admin,
            employee_code=f"EMP-{number:04d}",
            full_name=f"Person {number:02d}",
            email=f"person{number}@example.com",
        )
    alpha = db.session.scalar(select(Employee).where(Employee.employee_code == "EMP-0001"))
    beta = db.session.scalar(select(Employee).where(Employee.employee_code == "EMP-0002"))
    db.session.add(Posting(
        id=new_id(), employee_id=alpha.id, facility_id=facility.id, designation_id=officer.id,
        start_date=date(2020, 1, 1), end_date=None, order_reference="ORD", version=1,
    ))
    db.session.commit()

    search = admin.get("/employees?q=person+01")
    assert search.status_code == 200
    assert b"Person 01" in search.data
    assert b"Person 03" not in search.data
    filtered = admin.get(f"/employees?designation_id={officer.id}")
    assert b"EMP-0001" in filtered.data
    assert b"EMP-0002" not in filtered.data
    page = admin.get("/employees?page=2&sort=code&dir=asc")
    assert b"EMP-0021" in page.data
    assert b"EMP-0001" not in page.data
    unsafe = admin.get("/employees?sort=name;drop&dir=sideways")
    assert unsafe.status_code == 200
    ordered = admin.get("/employees?sort=name&dir=desc")
    body = ordered.get_data(as_text=True)
    assert body.find("Person 25") < body.find("Person 24")
    assert beta.id != alpha.id


def test_dashboard_uses_aggregates(admin):
    create_employee(admin, employee_code="EMP-D1", full_name="Dash One")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        response = admin.get("/")
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    body = response.get_data()
    assert b"Total employees" in body
    assert b"Employees by facility" in body
    assert b"Northview PHC" in body
    assert b"Gender" in body
    assert b"Female" in body
    assert b"Recently added" in body
    assert b"Dash One" in body
    joined = "\n".join(statements).lower()
    assert "count(" in joined
    assert "group by" in joined


def test_roster_name_without_birth_date_or_phone_can_be_opened(admin):
    facility = _facility()
    employee = Employee(
        id=new_id(),
        employee_code="EMP-ROSTER",
        full_name="Dr. Example Roster",
        date_of_birth=None,
        gender=None,
        phone=None,
        facility_id=facility.id,
        custom_data={},
        is_active=True,
        version=1,
    )
    db.session.add(employee)
    db.session.commit()
    page = admin.get(f"/employees/{employee.id}/personal")
    assert page.status_code == 200
    assert b"Dr. Example Roster" in page.data
    rejected = admin.post(
        f"/employees/{employee.id}/personal",
        data=payload(
            full_name="Dr. Example Roster",
            date_of_birth="",
            gender="",
            phone="",
            version="1",
            facility_id=str(facility.id),
        ),
    )
    assert rejected.status_code == 400
    stored = db.session.get(Employee, employee.id)
    assert stored.phone is None
    assert stored.date_of_birth is None
