import uuid

from sqlalchemy import select

from app.extensions import db
from app.models import Employee, Facility
from app.persistence import new_id
from app.workflow_data import (
    EMPLOYEE_COUNT,
    assert_isolated_test_database,
    populate_workflow_dataset,
    remove_workflow_dataset,
    workflow_employee_count,
)


def test_opd_workbook_has_forty_five_doctor_names():
    from pathlib import Path

    from app.roster_import import read_opd_rows

    rows = read_opd_rows(Path("OPD Reporting September.xlsx"))
    assert len(rows) == 45
    assert rows[0]["name"] == "Dr. Pawan Kumar"
    assert rows[0]["facility"] == "Sadar Hospital, Latehar"
    assert all(row["name"] and row["facility"] for row in rows)


def test_initial_migration_locks_down_unqualified_table_names():
    from pathlib import Path

    text = Path("migrations/versions/0001_initial.py").read_text(encoding="utf-8")
    assert "table.name" in text
    assert "sorted(db.metadata.tables)" not in text


def test_seed_refuses_supabase_and_requires_the_local_database_name():
    try:
        assert_isolated_test_database("postgresql://user:secret@db.example.supabase.co:5432/postgres", "1")
    except SystemExit as exc:
        assert "Supabase" in str(exc)
    else:
        raise AssertionError("Supabase URL was accepted.")
    try:
        assert_isolated_test_database("postgresql://user:secret@127.0.0.1:54329/hrms_test", None)
    except SystemExit as exc:
        assert "HRMS_TEST_DATABASE" in str(exc)
    else:
        raise AssertionError("Missing flag was accepted.")
    try:
        assert_isolated_test_database("postgresql://user:secret@127.0.0.1:54329/postgres", "1")
    except SystemExit as exc:
        assert "hrms_test" in str(exc)
    else:
        raise AssertionError("Wrong database name was accepted.")
    assert_isolated_test_database("postgresql+psycopg://user:secret@127.0.0.1:54329/hrms_test", "1")


def test_workflow_seed_is_idempotent_and_cleanup_leaves_other_rows(app):
    actor = uuid.UUID(app.config["ADMIN_USER_ID"])
    first = populate_workflow_dataset(actor)
    assert str(EMPLOYEE_COUNT) in first
    assert workflow_employee_count() == EMPLOYEE_COUNT
    second = populate_workflow_dataset(actor)
    assert "already present" in second
    assert workflow_employee_count() == EMPLOYEE_COUNT

    shared = list(db.session.scalars(select(Employee).where(Employee.full_name == "WF Shared Name")))
    assert len(shared) == 3
    assert len({row.employee_code for row in shared}) == 3
    hindi = db.session.scalar(select(Employee).where(Employee.full_name == "WF राधा कुमारी"))
    assert hindi is not None
    apostrophe = db.session.scalar(select(Employee).where(Employee.employee_code == "WF-0002"))
    assert "O'Brien" in apostrophe.full_name
    legacy_phone = db.session.scalar(select(Employee).where(Employee.employee_code == "WF-0098"))
    legacy_facility = db.session.scalar(select(Employee).where(Employee.employee_code == "WF-0099"))
    orphan = db.session.scalar(select(Employee).where(Employee.employee_code == "WF-0100"))
    assert legacy_phone.phone in (None, "")
    assert legacy_facility.facility_id is None
    assert orphan.custom_data.get("orphan_not_a_field") == "legacy fixture, not a defined field"
    assert all(row.phone is None or (len(row.phone) == 10 and row.phone[0] in "6789") for row in db.session.scalars(select(Employee).where(Employee.employee_code.like("WF-%"), Employee.phone.is_not(None))))

    keeper = Facility(
        id=new_id(),
        name="Kept Clinic",
        code="KEEP-1",
        facility_type="Clinic",
        district="Kept",
        is_active=True,
        version=1,
    )
    kept = Employee(
        id=new_id(),
        employee_code="KEEP-0001",
        full_name="Kept Person",
        date_of_birth=hindi.date_of_birth,
        gender="female",
        phone="9867000001",
        facility_id=keeper.id,
        custom_data={},
        is_active=True,
        version=1,
    )
    db.session.add_all([keeper, kept])
    db.session.commit()

    remove_workflow_dataset()
    assert workflow_employee_count() == 0
    assert db.session.get(Employee, kept.id) is not None
    assert db.session.scalar(select(Facility).where(Facility.code == "WF-NORTH")) is None
