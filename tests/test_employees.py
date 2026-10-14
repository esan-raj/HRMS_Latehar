from datetime import date

from sqlalchemy import event, func, select

from app.extensions import db
from app.models import (
    AuditEvent,
    Department,
    Designation,
    EducationRecord,
    Employee,
    EmploymentDetail,
    EmploymentType,
    Facility,
    Posting,
    Qualification,
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
    return db.session.scalar(select(Employee).where(Employee.employee_code == data["employee_code"].upper()))


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


def test_duplicate_employee_code_is_rejected(admin):
    create_employee(admin, employee_code="EMP-2001")
    before = db.session.scalar(select(func.count()).select_from(AuditEvent))
    facility = db.session.scalar(select(Facility).limit(1))
    duplicate = admin.post(
        "/employees/new",
        data=payload(employee_code="emp-2001", full_name="Other Person", version="0", facility_id=str(facility.id)),
    )
    assert duplicate.status_code == 400
    assert b"already exists" in duplicate.data
    assert b"Other Person" in duplicate.data
    assert db.session.scalar(select(func.count()).select_from(Employee)) == 1
    assert db.session.scalar(select(func.count()).select_from(AuditEvent)) == before


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
    assert created.changes["employee_code"]["after"] == "EMP-AUD1"
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


def test_education_record_crud(admin):
    employee = create_employee(admin)
    qualification = Qualification(id=new_id(), name="MBBS", is_active=True, version=1)
    db.session.add(qualification)
    db.session.commit()
    created = admin.post(
        f"/employees/{employee.id}/education",
        data={
            "qualification_id": str(qualification.id),
            "specialization": "Medicine",
            "institution": "Northview Medical College",
            "passing_year": "2011",
            "version": "0",
        },
    )
    assert created.status_code == 302
    record = db.session.scalar(select(EducationRecord))
    edited = admin.post(
        f"/employees/{employee.id}/education/{record.id}/edit",
        data={
            "qualification_id": str(qualification.id),
            "specialization": "Community medicine",
            "institution": "Northview Medical College",
            "passing_year": "2011",
            "version": str(record.version),
        },
    )
    assert edited.status_code == 302
    record = db.session.get(EducationRecord, record.id)
    assert record.specialization == "Community medicine"
    confirm = admin.get(f"/employees/{employee.id}/education/{record.id}/delete")
    assert confirm.status_code == 200
    assert db.session.get(EducationRecord, record.id) is not None
    removed = admin.post(
        f"/employees/{employee.id}/education/{record.id}/delete",
        data={"version": str(record.version)},
    )
    assert removed.status_code == 302
    assert db.session.get(EducationRecord, record.id) is None
    audit = db.session.scalar(select(AuditEvent).where(AuditEvent.entity_type == "education", AuditEvent.action == "delete"))
    assert audit is not None


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
    second = admin.post(f"/employees/{employee.id}/postings", data=posting(other.id, "2021-01-01", ""))
    assert second.status_code == 400
    assert b"already has an open posting" in second.data
    overlap = admin.post(f"/employees/{employee.id}/postings", data=posting(other.id, "2019-01-01", "2020-06-01"))
    assert overlap.status_code == 400
    assert b"overlaps" in overlap.data
    backwards = admin.post(f"/employees/{employee.id}/postings", data=posting(other.id, "2018-05-02", "2018-05-01"))
    assert backwards.status_code == 400
    earlier = admin.post(f"/employees/{employee.id}/postings", data=posting(other.id, "2018-01-01", "2019-12-31"))
    assert earlier.status_code == 302
    open_rows = db.session.scalars(select(Posting).where(Posting.end_date.is_(None))).all()
    assert len(open_rows) == 1
    assert open_rows[0].start_date == date(2020, 1, 1)


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
    assert b"EMP-0001" in search.data
    assert b"EMP-0003" not in search.data
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
    assert b"Total employees" in response.data
    joined = "\n".join(statements).lower()
    assert "count(" in joined
    assert "group by" in joined
