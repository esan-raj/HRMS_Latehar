import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.domain import SelectionError
from app.extensions import db
from app.hr import delete_employees
from app.models import (
    Address,
    AuditEvent,
    Department,
    Designation,
    Employee,
    EmploymentDetail,
    EmploymentType,
    Facility,
    JobAppointment,
    Posting,
)
from app.persistence import new_id
from tests.conftest import ADMIN_ID, CsrfConfig, authenticate
from tests.test_employees import create_employee


def _history(employee):
    facility = db.session.get(Facility, employee.facility_id)
    department = Department(id=new_id(), name="Clinical", is_active=True, version=1)
    designation = Designation(id=new_id(), name="Medical Officer", is_active=True, version=1)
    employment_type = EmploymentType(id=new_id(), name="Regular", is_active=True, version=1)
    db.session.add_all([department, designation, employment_type])
    db.session.flush()
    detail = EmploymentDetail(
        id=new_id(),
        employee_id=employee.id,
        department_id=department.id,
        employment_type_id=employment_type.id,
        service_joining_date=date(2020, 1, 1),
        pay_level="L10",
        version=1,
    )
    posting = Posting(
        id=new_id(),
        employee_id=employee.id,
        facility_id=facility.id,
        designation_id=designation.id,
        start_date=date(2020, 1, 1),
        order_reference="ORD-9",
        version=1,
    )
    appointment = JobAppointment(
        id=new_id(),
        employee_id=employee.id,
        appointment_reference="APT-9",
        appointment_date=date(2020, 1, 1),
        joining_date=date(2020, 1, 2),
        designation_id=designation.id,
        employment_type_id=employment_type.id,
        version=1,
    )
    address = Address(
        id=new_id(),
        employee_id=employee.id,
        address_type="present",
        line1="Ward 2",
        district="Latehar",
        state="Jharkhand",
        pin_code="829206",
        version=1,
    )
    db.session.add_all([detail, posting, appointment, address])
    db.session.commit()
    return facility, designation, department, employment_type, detail, posting, appointment, address


def _delete(admin, employee):
    return admin.post(f"/employees/{employee.id}/delete", data={"version": str(employee.version)})


def test_active_and_inactive_employees_can_be_deleted(admin):
    active = create_employee(admin, full_name="Active Delete")
    inactive = create_employee(admin, full_name="Inactive Delete")
    deactivated = admin.post(f"/employees/{inactive.id}/deactivate", data={"version": str(inactive.version)})
    assert deactivated.status_code == 302
    inactive = db.session.get(Employee, inactive.id)
    assert inactive.is_active is False
    for employee in (active, inactive):
        page = admin.get(f"/employees/{employee.id}/delete")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert employee.employee_code in body
        assert "related HR records" in body
        removed = _delete(admin, employee)
        assert removed.status_code == 302
        assert db.session.get(Employee, employee.id) is None


def test_related_rows_go_and_shared_masters_remain(admin):
    employee = create_employee(admin, full_name="History Delete", phone="9867001122")
    facility, designation, department, employment_type, detail, posting, appointment, address = _history(employee)
    removed = _delete(admin, employee)
    assert removed.status_code == 302
    assert db.session.get(EmploymentDetail, detail.id) is None
    assert db.session.get(Posting, posting.id) is None
    assert db.session.get(JobAppointment, appointment.id) is None
    assert db.session.get(Address, address.id) is None
    assert db.session.get(Facility, facility.id) is not None
    assert db.session.get(Designation, designation.id) is not None
    assert db.session.get(Department, department.id) is not None
    assert db.session.get(EmploymentType, employment_type.id) is not None
    audit = db.session.scalar(
        select(AuditEvent).where(AuditEvent.entity_id == employee.id, AuditEvent.action == "delete")
    )
    assert audit is not None
    assert audit.actor_id == uuid.UUID(ADMIN_ID)
    assert audit.created_at is not None
    assert audit.changes["employee_code"]["before"] == employee.employee_code
    assert "phone" not in audit.changes
    assert "full_name" not in audit.changes
    assert "9867001122" not in str(audit.changes)


def test_bulk_selection_confirms_then_deletes_the_current_page(admin):
    first = create_employee(admin, full_name="Bulk One")
    second = create_employee(admin, full_name="Bulk Two")
    kept = create_employee(admin, full_name="Bulk Kept")
    listing = admin.get("/employees/")
    body = listing.get_data(as_text=True)
    assert "Select all applies only to this page" in body
    assert "Delete selected" in body
    assert 'id="delete-selected-button" disabled' in body or "disabled" in body
    data = {
        "selected": [str(first.id), str(second.id)],
        f"version_{first.id}": str(first.version),
        f"version_{second.id}": str(second.version),
        "q": "Bulk",
    }
    preview = admin.post("/employees/delete-selected", data=data)
    assert preview.status_code == 200
    text = preview.get_data(as_text=True)
    assert first.employee_code in text
    assert second.employee_code in text
    assert kept.employee_code not in text
    assert db.session.get(Employee, first.id) is not None
    confirmed = admin.post("/employees/delete-selected", data={**data, "confirm": "yes"})
    assert confirmed.status_code == 302
    assert "q=Bulk" in confirmed.headers["Location"]
    assert db.session.get(Employee, first.id) is None
    assert db.session.get(Employee, second.id) is None
    assert db.session.get(Employee, kept.id) is not None


def test_bulk_delete_rejects_empty_invalid_duplicate_and_stale_ids(admin):
    employee = create_employee(admin, full_name="Stale Target")
    empty = admin.post("/employees/delete-selected", data={"confirm": "yes"})
    assert empty.status_code == 302
    assert db.session.get(Employee, employee.id) is not None
    invalid = admin.post(
        "/employees/delete-selected",
        data={"confirm": "yes", "selected": "not-an-id", "version_not-an-id": "1"},
    )
    assert invalid.status_code == 302
    duplicate = admin.post(
        "/employees/delete-selected",
        data={
            "confirm": "yes",
            "selected": [str(employee.id), str(employee.id)],
            f"version_{employee.id}": str(employee.version),
        },
    )
    assert duplicate.status_code == 302
    stale = admin.post(
        "/employees/delete-selected",
        data={"confirm": "yes", "selected": str(employee.id), f"version_{employee.id}": "99"},
    )
    assert stale.status_code == 302
    assert db.session.get(Employee, employee.id) is not None
    missing = str(uuid.uuid4())
    absent = admin.post(
        "/employees/delete-selected",
        data={"confirm": "yes", "selected": missing, f"version_{missing}": "1"},
    )
    assert absent.status_code == 302
    assert db.session.get(Employee, employee.id) is not None
    assert admin.get("/employees/delete-selected").status_code == 405
    before = db.session.scalar(select(db.func.count()).select_from(Employee))
    with pytest.raises(SelectionError):
        delete_employees(ADMIN_ID, [(str(uuid.uuid4()), "1") for _ in range(21)])
    after = db.session.scalar(select(db.func.count()).select_from(Employee))
    assert after == before


def test_failed_batch_deletes_nobody(admin, monkeypatch):
    first = create_employee(admin, full_name="Rollback One")
    second = create_employee(admin, full_name="Rollback Two")

    def fail():
        raise RuntimeError("disk failed")

    monkeypatch.setattr(db.session, "commit", fail)
    response = admin.post(
        "/employees/delete-selected",
        data={
            "confirm": "yes",
            "selected": [str(first.id), str(second.id)],
            f"version_{first.id}": str(first.version),
            f"version_{second.id}": str(second.version),
        },
    )
    assert response.status_code == 302
    monkeypatch.undo()
    db.session.rollback()
    assert db.session.get(Employee, first.id) is not None
    assert db.session.get(Employee, second.id) is not None
    deleted = db.session.scalar(
        select(AuditEvent).where(AuditEvent.action == "delete", AuditEvent.entity_id.in_([first.id, second.id]))
    )
    assert deleted is None


def test_delete_requires_sign_in(admin, app):
    employee = create_employee(admin, full_name="Guarded Person")
    outsider = app.test_client()
    denied = outsider.post(f"/employees/{employee.id}/delete", data={"version": str(employee.version)})
    assert denied.status_code == 302
    assert "/login" in denied.headers["Location"]
    assert db.session.get(Employee, employee.id) is not None
    stale = admin.post(f"/employees/{employee.id}/delete", data={"version": "99"})
    assert stale.status_code == 302
    assert db.session.get(Employee, employee.id) is not None


def test_delete_post_requires_csrf():
    from app import create_app
    from app.hr import create_employee as save_employee
    from tests.test_employees import _facility

    application = create_app(CsrfConfig)
    with application.app_context():
        db.create_all()
        facility = _facility()
        employee = save_employee(
            ADMIN_ID,
            {
                "full_name": "Csrf Person",
                "phone": "9867543299",
                "facility_id": facility.id,
                "is_active": True,
                "custom_data": {},
            },
        )
        client = application.test_client()
        authenticate(client, application)
        rejected = client.post(f"/employees/{employee.id}/delete", data={"version": str(employee.version)})
        assert rejected.status_code == 400
        assert db.session.get(Employee, employee.id) is not None
        db.session.remove()
        db.drop_all()
