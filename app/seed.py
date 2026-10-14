import uuid
from datetime import date

from app.audit import column_snapshot, record_audit
from app.extensions import db
from app.hr import create_employee, save_addresses, save_employment, save_posting, create_child
from app.models import (
    Department,
    Designation,
    Employee,
    EmploymentType,
    Facility,
    LeaveType,
    Qualification,
    TrainingType,
)
from app.persistence import new_id
from app.record_routes import (
    ALLEGATION_FIELDS,
    APPOINTMENT_FIELDS,
    DACP_FIELDS,
    EDUCATION_FIELDS,
    LEAVE_FIELDS,
    PROMOTION_FIELDS,
    TRAINING_FIELDS,
)
from app.models import Allegation, DacpDetail, EducationRecord, JobAppointment, LeaveRecord, Promotion, TrainingRecord


def register_commands(app) -> None:
    @app.cli.command("seed-demo")
    def seed_demo():
        """Insert a small set of synthetic employee records."""
        if db.session.query(Employee).first() is not None:
            raise SystemExit("Demo data was not loaded because employees already exist.")
        actor = uuid.UUID(app.config["ADMIN_USER_ID"])
        clinical = _master(actor, Department, "department", name="Clinical Services")
        _master(actor, Department, "department", name="Nursing")
        officer = _master(actor, Designation, "designation", name="Medical Officer")
        nurse = _master(actor, Designation, "designation", name="Staff Nurse")
        _master(actor, Designation, "designation", name="Laboratory Technician")
        phc = _master(
            actor,
            Facility,
            "facility",
            name="Northview Primary Health Centre",
            code="PHC-NORTH",
            facility_type="PHC",
            district="Northview",
            block="Ward 2",
            fields=("name", "code", "facility_type", "district", "block", "is_active"),
        )
        chc = _master(
            actor,
            Facility,
            "facility",
            name="Rivermark Community Health Centre",
            code="CHC-RIVER",
            facility_type="CHC",
            district="Rivermark",
            block=None,
            fields=("name", "code", "facility_type", "district", "block", "is_active"),
        )
        regular = _master(actor, EmploymentType, "employment_type", name="Regular")
        _master(actor, EmploymentType, "employment_type", name="Contract")
        mbbs = _master(actor, Qualification, "qualification", name="MBBS")
        gnm = _master(actor, Qualification, "qualification", name="GNM")
        induction = _master(actor, TrainingType, "training_type", name="Induction")
        earned = _master(actor, LeaveType, "leave_type", name="Earned Leave")
        _master(actor, LeaveType, "leave_type", name="Casual Leave")

        asha = create_employee(
            actor,
            {
                "employee_code": "EMP-1001",
                "full_name": "Asha Menon",
                "date_of_birth": date(1988, 4, 12),
                "gender": "female",
                "phone": "9876111222",
                "email": "asha.menon@example.com",
                "facility_id": phc.id,
                "is_active": True,
            },
        )
        save_addresses(
            actor,
            asha.id,
            {"line1": "12 Lake Road", "line2": None, "district": "Northview", "state": "Karnataka", "pin_code": "560001"},
            0,
            {"line1": "45 Temple Street", "line2": None, "district": "Rivermark", "state": "Kerala", "pin_code": "682001"},
            0,
        )
        create_child(
            actor,
            asha.id,
            EducationRecord,
            "education",
            EDUCATION_FIELDS,
            {"qualification_id": mbbs.id, "specialization": "General medicine", "institution": "Northview Medical College", "passing_year": 2011},
        )
        create_child(
            actor,
            asha.id,
            TrainingRecord,
            "training",
            TRAINING_FIELDS,
            {
                "training_type_id": induction.id,
                "title": "Primary care induction",
                "provider": "State Institute of Health",
                "start_date": date(2016, 1, 4),
                "end_date": date(2016, 1, 15),
                "remarks": None,
            },
        )
        create_child(
            actor,
            asha.id,
            JobAppointment,
            "job_appointment",
            APPOINTMENT_FIELDS,
            {
                "appointment_reference": "APT-2016-014",
                "appointment_date": date(2015, 12, 20),
                "joining_date": date(2016, 1, 1),
                "designation_id": officer.id,
                "employment_type_id": regular.id,
            },
        )
        save_employment(
            actor,
            asha.id,
            0,
            {
                "department_id": clinical.id,
                "employment_type_id": regular.id,
                "service_joining_date": date(2016, 1, 1),
                "retirement_date": date(2048, 4, 30),
                "pay_level": "Level 10",
            },
        )
        save_posting(
            actor,
            asha.id,
            None,
            0,
            {
                "facility_id": chc.id,
                "designation_id": officer.id,
                "start_date": date(2016, 1, 1),
                "end_date": date(2019, 12, 31),
                "order_reference": "POST-2016-014",
                "remarks": None,
            },
        )
        save_posting(
            actor,
            asha.id,
            None,
            0,
            {
                "facility_id": phc.id,
                "designation_id": officer.id,
                "start_date": date(2020, 1, 1),
                "end_date": None,
                "order_reference": "POST-2020-003",
                "remarks": "Current posting",
            },
        )
        create_child(
            actor,
            asha.id,
            Promotion,
            "promotion",
            PROMOTION_FIELDS,
            {
                "previous_designation_id": nurse.id,
                "new_designation_id": officer.id,
                "effective_date": date(2016, 1, 1),
                "order_reference": "PROM-2016-002",
                "remarks": None,
            },
        )
        create_child(
            actor,
            asha.id,
            Allegation,
            "allegation",
            ALLEGATION_FIELDS,
            {
                "reference_no": "ALG-2022-009",
                "description": "Synthetic recordkeeping entry for demonstration.",
                "recorded_date": date(2022, 6, 1),
                "status": "open",
                "resolution_date": None,
                "remarks": None,
            },
        )
        create_child(
            actor,
            asha.id,
            LeaveRecord,
            "leave",
            LEAVE_FIELDS,
            {
                "leave_type_id": earned.id,
                "start_date": date(2024, 5, 2),
                "end_date": date(2024, 5, 6),
                "status": "recorded",
                "remarks": None,
            },
        )
        create_child(
            actor,
            asha.id,
            DacpDetail,
            "dacp",
            DACP_FIELDS,
            {
                "stage": "Stage 1",
                "effective_date": date(2021, 1, 1),
                "order_reference": "DACP-2021-001",
                "remarks": None,
            },
        )
        create_employee(
            actor,
            {
                "employee_code": "EMP-1002",
                "full_name": "Ravi Deshmukh",
                "date_of_birth": date(1992, 11, 3),
                "gender": "male",
                "phone": "9876000011",
                "email": "ravi.deshmukh@example.com",
                "facility_id": chc.id,
                "is_active": True,
            },
        )
        leela = create_employee(
            actor,
            {
                "employee_code": "EMP-1003",
                "full_name": "Leela Thomas",
                "date_of_birth": date(1985, 2, 19),
                "gender": "female",
                "phone": "9876000022",
                "email": None,
                "facility_id": chc.id,
                "is_active": False,
            },
        )
        create_child(
            actor,
            leela.id,
            EducationRecord,
            "education",
            EDUCATION_FIELDS,
            {"qualification_id": gnm.id, "specialization": None, "institution": "Rivermark School of Nursing", "passing_year": 2007},
        )
        print("Created 3 synthetic employees.")


def _master(actor, model, entity, fields=("name", "is_active"), **values):
    row = model(id=new_id(), version=1, is_active=True, **values)
    db.session.add(row)
    record_audit(actor, entity, row.id, "create", {}, column_snapshot(row, fields))
    db.session.commit()
    return row
