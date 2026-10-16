"""Synthetic workflow dataset for the isolated local test database.

The CLI refuses to write unless HRMS_TEST_DATABASE=1 and DATABASE_URL points at
127.0.0.1 or localhost, database name hrms_test. It does not connect to Supabase.
"""

from datetime import date, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.orm.attributes import flag_modified

from app.audit import column_snapshot, record_audit
from app.extensions import db
from app.fielddefs import create_custom_field, retire_option, set_archived
from app.hr import create_child, create_employee, save_employment, save_posting
from app.models import (
    Address,
    Allegation,
    AuditEvent,
    DacpDetail,
    Department,
    Designation,
    EducationRecord,
    Employee,
    EmployeeField,
    EmploymentDetail,
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

DATASET = "WF"
EMPLOYEE_COUNT = 100
LEGACY_NOTES = (
    "WF-0098 has phone cleared after a valid create. The personal form rejects a missing mobile; "
    "this row is a legacy incomplete fixture, not a normal insert.",
    "WF-0099 has facility_id cleared after a valid create. The personal form rejects a missing facility.",
    "WF-0100 stores custom key orphan_not_a_field. That key is not an employee field.",
    "WF-0095 omits the required WF Shift key. WF-0096 stores JSON null. WF-0097 stores a blank string.",
    "Leading zeros and spaced mobile numbers are rejected by the form, so they are not seeded.",
)


def assert_isolated_test_database(database_url: str, flag: str | None) -> None:
    """Stop before any write when the target is not the local test database."""
    if (flag or "").strip() != "1":
        raise SystemExit("Refusing to write: set HRMS_TEST_DATABASE=1 for the isolated test database.")
    text = (database_url or "").strip()
    lowered = text.lower()
    if "supabase" in lowered or "pooler." in lowered:
        raise SystemExit("Refusing to write: the database URL points at Supabase.")
    parts = urlsplit(text.replace("postgresql+psycopg://", "postgresql://", 1))
    host = (parts.hostname or "").lower()
    name = parts.path.strip("/").split("/")[-1]
    if host not in {"127.0.0.1", "localhost"} or name != "hrms_test":
        raise SystemExit(
            "Refusing to write: expected host 127.0.0.1 or localhost and database hrms_test. "
            f"Saw host {host or '(none)'} and database {name or '(none)'}."
        )


def workflow_employee_count() -> int:
    return int(
        db.session.scalar(select(func.count()).select_from(Employee).where(Employee.employee_code.like("WF-%")))
        or 0
    )


def populate_workflow_dataset(actor_id) -> str:
    """Insert the dataset once. A later call leaves an existing complete dataset in place."""
    count = workflow_employee_count()
    marker = db.session.scalar(select(Employee.id).where(Employee.employee_code == "WF-0001"))
    if marker is not None and count == EMPLOYEE_COUNT:
        return f"Workflow dataset already present ({count} employees)."
    if count:
        remove_workflow_dataset()
    _build(actor_id)
    return f"Loaded {workflow_employee_count()} synthetic workflow employees."


def remove_workflow_dataset() -> str:
    """Delete only rows that belong to this dataset."""
    employees = list(db.session.scalars(select(Employee).where(Employee.employee_code.like("WF-%"))))
    employee_ids = [row.id for row in employees]
    facilities = list(db.session.scalars(select(Facility).where(Facility.name.like("WF %"))))
    facility_ids = [row.id for row in facilities]
    if employee_ids and facility_ids:
        outsider = db.session.scalar(
            select(Employee.id).where(Employee.facility_id.in_(facility_ids), Employee.id.not_in(employee_ids)).limit(1)
        )
        if outsider is not None:
            raise SystemExit("Refusing cleanup: a non-workflow employee uses a workflow facility.")
    field_ids = list(
        db.session.scalars(select(EmployeeField.id).where(EmployeeField.label.like("WF %"), EmployeeField.is_system.is_(False)))
    )
    master_ids = _master_ids()
    child_models = (
        Address,
        EducationRecord,
        TrainingRecord,
        JobAppointment,
        EmploymentDetail,
        Posting,
        Promotion,
        Allegation,
        LeaveRecord,
        DacpDetail,
    )
    removed_ids = list(employee_ids)
    for model in child_models:
        if not employee_ids:
            break
        child_ids = list(db.session.scalars(select(model.id).where(model.employee_id.in_(employee_ids))))
        removed_ids.extend(child_ids)
        db.session.execute(delete(model).where(model.employee_id.in_(employee_ids)))
    if employee_ids:
        db.session.execute(delete(Employee).where(Employee.id.in_(employee_ids)))
    if field_ids:
        from app.models import EmployeeFieldOption

        db.session.execute(delete(EmployeeFieldOption).where(EmployeeFieldOption.field_id.in_(field_ids)))
        db.session.execute(delete(EmployeeField).where(EmployeeField.id.in_(field_ids)))
        removed_ids.extend(field_ids)
    _delete_unreferenced_masters()
    removed_ids.extend(master_ids)
    if removed_ids:
        db.session.execute(delete(AuditEvent).where(AuditEvent.entity_id.in_(removed_ids)))
    db.session.commit()
    return f"Removed {len(employee_ids)} workflow employees."


def _build(actor_id) -> None:
    facilities = {
        "north": _facility(actor_id, "WF North Clinic", "WF-NORTH", "Clinic", "Northview", "Ward 1", True),
        "river": _facility(actor_id, "WF River Hospital", "WF-RIVER", "Hospital", "Rivermark", None, True),
        "hill": _facility(actor_id, "WF Hill Dispensary", "WF-HILL", "Dispensary", "Hillside", "Block B", True),
        "unused": _facility(actor_id, "WF Unused Clinic", "WF-UNUSED", "Clinic", "Unused", None, True),
        "closed": _facility(actor_id, "WF Closed Ward", "WF-CLOSED", "Ward", "Oldtown", None, False),
        "spare": _facility(actor_id, "WF Spare Block", "WF-SPARE", "Block", "Spare", None, False),
    }
    departments = {
        "medicine": _named(actor_id, Department, "department", "WF Medicine", True),
        "nursing": _named(actor_id, Department, "department", "WF Nursing", True),
        "unused": _named(actor_id, Department, "department", "WF Unused Records", True),
        "retired": _named(actor_id, Department, "department", "WF Retired Admin", False),
        "closed": _named(actor_id, Department, "department", "WF Unused Closed", False),
    }
    designations = {
        "officer": _named(actor_id, Designation, "designation", "WF Medical Officer", True),
        "nurse": _named(actor_id, Designation, "designation", "WF Staff Nurse", True),
        "tech": _named(actor_id, Designation, "designation", "WF Technician", True),
        "unused": _named(actor_id, Designation, "designation", "WF Unused Clerk", True),
        "former": _named(actor_id, Designation, "designation", "WF Former Matron", False),
        "retired": _named(actor_id, Designation, "designation", "WF Unused Retired", False),
    }
    employment_types = {
        "regular": _named(actor_id, EmploymentType, "employment_type", "WF Regular", True),
        "contract": _named(actor_id, EmploymentType, "employment_type", "WF Contract", True),
        "unused": _named(actor_id, EmploymentType, "employment_type", "WF Unused Honorary", True),
        "bond": _named(actor_id, EmploymentType, "employment_type", "WF Old Bond", False),
        "ended": _named(actor_id, EmploymentType, "employment_type", "WF Unused Ended", False),
    }
    qualifications = {
        "mbbs": _named(actor_id, Qualification, "qualification", "WF MBBS", True),
        "gnm": _named(actor_id, Qualification, "qualification", "WF GNM", True),
        "unused": _named(actor_id, Qualification, "qualification", "WF Unused Diploma", True),
        "old": _named(actor_id, Qualification, "qualification", "WF Old Certificate", False),
    }
    training_types = {
        "induction": _named(actor_id, TrainingType, "training_type", "WF Induction", True),
        "unused": _named(actor_id, TrainingType, "training_type", "WF Unused Seminar", True),
        "old": _named(actor_id, TrainingType, "training_type", "WF Retired Course", False),
    }
    leave_types = {
        "earned": _named(actor_id, LeaveType, "leave_type", "WF Earned Leave", True),
        "unused": _named(actor_id, LeaveType, "leave_type", "WF Unused Leave", True),
        "old": _named(actor_id, LeaveType, "leave_type", "WF Retired Leave", False),
    }
    fields = _fields(actor_id)
    for index in range(1, EMPLOYEE_COUNT + 1):
        _employee(
            actor_id,
            index,
            facilities,
            departments,
            designations,
            employment_types,
            qualifications,
            training_types,
            leave_types,
            fields,
        )
    retire_option(actor_id, fields["shift"].id, fields["night"].id)


def _fields(actor_id) -> dict:
    help_text = "टिप्पणी — note, punctuation: (optional); 1/2."
    specs = [
        ("badge", "WF Badge note", "short_text", False, True, help_text, []),
        ("biography", "WF Biography", "long_text", False, False, "Long note. अधिकतम 4000.", []),
        ("ward", "WF Ward number", "integer", False, True, "Zero and negatives are allowed.", []),
        ("allowance", "WF Allowance (₹)", "decimal", False, True, "Zero is a present value.", []),
        ("review", "WF Review date", "date", False, False, None, []),
        ("shift", "WF Shift", "dropdown", True, True, "पाली / shift.", ["Morning", "Evening", "Night", "Unused swing"]),
        ("on_call", "WF On call", "yes_no", False, True, "No is a present value.", []),
        ("shelf", "WF Empty shelf", "short_text", False, True, "No employee stores this.", []),
        ("motto", "WF Archived motto", "short_text", False, False, "Archived after seeding.", []),
    ]
    created = {}
    for key, label, field_type, required, shown, help_value, options in specs:
        created[key] = create_custom_field(
            actor_id,
            {
                "label": label,
                "help_text": help_value,
                "placeholder": None,
                "field_type": field_type,
                "required": required,
                "section": "Personal Details",
                "show_in_register": shown,
            },
            options,
        )
    shift = created["shift"]
    by_label = {option.label: option for option in shift.options}
    created["morning"] = by_label["Morning"]
    created["evening"] = by_label["Evening"]
    created["night"] = by_label["Night"]
    created["unused_option"] = by_label["Unused swing"]
    set_archived(actor_id, created["motto"].id, True)
    return created


def _employee(actor_id, index, facilities, departments, designations, employment_types, qualifications, training_types, leave_types, fields) -> None:
    code = f"WF-{index:04d}"
    home_keys = ("north", "river", "hill")
    home = facilities[home_keys[index % 3]]
    active = index % 5 != 0
    phone = f"9{index:09d}"
    custom = _custom_values(index, fields)
    employee = create_employee(
        actor_id,
        {
            "employee_code": code,
            "full_name": _name(index),
            "date_of_birth": date(1975, 1, 1) + timedelta(days=index * 17),
            "gender": ("female", "male", "other")[index % 3],
            "phone": phone,
            "email": None if index % 2 == 0 else f"{code.lower()}@example.com",
            "facility_id": home.id,
            "is_active": active,
            "custom_data": custom,
        },
    )
    if index in {98, 99, 100}:
        _legacy(employee, index)
    if index > 90:
        return
    department = departments["retired"] if index == 12 else departments["medicine"] if index % 2 else departments["nursing"]
    employment_type = employment_types["bond"] if index == 12 else employment_types["regular"] if index % 2 else employment_types["contract"]
    save_employment(
        actor_id,
        employee.id,
        0,
        {
            "department_id": department.id,
            "employment_type_id": employment_type.id,
            "service_joining_date": date(2016, 1, 1),
            "retirement_date": None if index % 4 else date(2046, 1, 1),
            "pay_level": "L-10",
        },
    )
    if index % 7 == 0:
        return
    past_facility = facilities["closed"]
    current_facility = facilities["river"] if index % 4 == 0 else home
    save_posting(
        actor_id,
        employee.id,
        None,
        0,
        {
            "facility_id": past_facility.id,
            "designation_id": designations["former"].id,
            "start_date": date(2016, 1, 1),
            "end_date": date(2019, 12, 31),
            "order_reference": f"WF-PAST-{index:04d}",
            "remarks": None,
        },
    )
    save_posting(
        actor_id,
        employee.id,
        None,
        0,
        {
            "facility_id": current_facility.id,
            "designation_id": (designations["officer"] if index % 2 else designations["nurse"]).id,
            "start_date": date(2020, 1, 1),
            "end_date": None,
            "order_reference": f"WF-OPEN-{index:04d}",
            "remarks": "Synthetic current posting." if index != 11 else ("WF boundary " + ("ट" * 180)),
        },
    )
    if index == 11:
        _history(actor_id, employee, facilities, designations, employment_types, qualifications, training_types, leave_types)
    elif index <= 20:
        create_child(
            actor_id,
            employee.id,
            JobAppointment,
            "job_appointment",
            APPOINTMENT_FIELDS,
            {
                "appointment_reference": f"WF-APT-{index:04d}",
                "appointment_date": date(2016, 1, 1),
                "joining_date": date(2016, 1, 15),
                "designation_id": designations["tech"].id,
                "employment_type_id": employment_types["regular"].id,
            },
        )


def _custom_values(index: int, fields: dict) -> dict:
    values = {}
    shift_id = str(fields["shift"].id)
    if index == 96:
        values[shift_id] = None
    elif index == 97:
        values[shift_id] = ""
    elif index != 95:
        choice = fields["night"] if index % 9 == 0 else fields["morning"] if index % 2 else fields["evening"]
        values[shift_id] = str(choice.id)
    if index % 3 == 0:
        values[str(fields["badge"].id)] = "Badge — टिप्पणी"
    if index == 11:
        values[str(fields["biography"].id)] = "WF " + ("क" * 3997)
    elif index % 6 == 0:
        values[str(fields["biography"].id)] = "A shorter synthetic biography."
    if index % 5 == 0:
        values[str(fields["ward"].id)] = 0
    elif index % 5 == 1:
        values[str(fields["ward"].id)] = -3
    elif index % 5 == 2:
        values[str(fields["ward"].id)] = 12
    if index % 4 == 0:
        values[str(fields["allowance"].id)] = "0"
    elif index % 4 == 1:
        values[str(fields["allowance"].id)] = "-2.5"
    if index % 8 == 0:
        values[str(fields["review"].id)] = "2024-03-15"
    values[str(fields["on_call"].id)] = "no" if index % 2 == 0 else "yes"
    if index % 10 == 0:
        values[str(fields["motto"].id)] = "Kept after archive"
    return values


def _legacy(employee: Employee, index: int) -> None:
    if index == 98:
        employee.phone = None
    elif index == 99:
        employee.facility_id = None
    elif index == 100:
        data = dict(employee.custom_data or {})
        data["orphan_not_a_field"] = "legacy fixture, not a defined field"
        employee.custom_data = data
        flag_modified(employee, "custom_data")
    db.session.commit()


def _history(actor_id, employee, facilities, designations, employment_types, qualifications, training_types, leave_types) -> None:
    create_child(
        actor_id,
        employee.id,
        EducationRecord,
        "education",
        EDUCATION_FIELDS,
        {
            "qualification_id": qualifications["mbbs"].id,
            "specialization": "General",
            "institution": "WF Example College",
            "passing_year": 2011,
        },
    )
    create_child(
        actor_id,
        employee.id,
        TrainingRecord,
        "training",
        TRAINING_FIELDS,
        {
            "training_type_id": training_types["induction"].id,
            "title": "WF Primary induction",
            "provider": "WF Example Institute",
            "start_date": date(2016, 2, 1),
            "end_date": date(2016, 2, 5),
            "remarks": None,
        },
    )
    create_child(
        actor_id,
        employee.id,
        JobAppointment,
        "job_appointment",
        APPOINTMENT_FIELDS,
        {
            "appointment_reference": "WF-APT-0011",
            "appointment_date": date(2016, 1, 1),
            "joining_date": date(2016, 1, 15),
            "designation_id": designations["officer"].id,
            "employment_type_id": employment_types["regular"].id,
        },
    )
    create_child(
        actor_id,
        employee.id,
        Promotion,
        "promotion",
        PROMOTION_FIELDS,
        {
            "previous_designation_id": designations["tech"].id,
            "new_designation_id": designations["officer"].id,
            "effective_date": date(2019, 6, 1),
            "order_reference": "WF-PROM-0011",
            "remarks": None,
        },
    )
    create_child(
        actor_id,
        employee.id,
        Allegation,
        "allegation",
        ALLEGATION_FIELDS,
        {
            "reference_no": "WF-ALG-0011",
            "description": "Synthetic recordkeeping entry.",
            "recorded_date": date(2021, 4, 1),
            "status": "closed",
            "resolution_date": date(2021, 5, 1),
            "remarks": None,
        },
    )
    create_child(
        actor_id,
        employee.id,
        LeaveRecord,
        "leave",
        LEAVE_FIELDS,
        {
            "leave_type_id": leave_types["earned"].id,
            "start_date": date(2023, 5, 2),
            "end_date": date(2023, 5, 4),
            "status": "recorded",
            "remarks": None,
        },
    )
    create_child(
        actor_id,
        employee.id,
        DacpDetail,
        "dacp",
        DACP_FIELDS,
        {
            "stage": "WF Stage 1",
            "effective_date": date(2022, 1, 1),
            "order_reference": "WF-DACP-0011",
            "remarks": None,
        },
    )
    from app.hr import save_addresses

    save_addresses(
        actor_id,
        employee.id,
        {"line1": "12 Example Road", "line2": None, "district": "Northview", "state": "Jharkhand", "pin_code": "829201"},
        0,
        {"line1": "45 Example Street", "line2": "", "district": "Rivermark", "state": "Jharkhand", "pin_code": "829202"},
        0,
    )


def _name(index: int) -> str:
    special = {
        1: "WF Anne Marie Cole",
        2: "WF Sean O'Brien",
        3: "WF Jean-Luc Moreau",
        4: "WF राधा कुमारी",
        5: "WF Shared Name",
        6: "WF Shared Name",
        7: "WF Shared Name",
        8: "WF " + ("क" * 197),
    }
    return special.get(index, f"WF Person {index:03d}")


def _named(actor_id, model, entity, name, active):
    row = model(id=new_id(), version=1, name=name, is_active=active)
    db.session.add(row)
    record_audit(actor_id, entity, row.id, "create", {}, column_snapshot(row, ("name", "is_active")))
    db.session.commit()
    return row


def _facility(actor_id, name, code, facility_type, district, block, active):
    row = Facility(
        id=new_id(),
        version=1,
        name=name,
        code=code,
        facility_type=facility_type,
        district=district,
        block=block,
        is_active=active,
    )
    db.session.add(row)
    record_audit(
        actor_id,
        "facility",
        row.id,
        "create",
        {},
        column_snapshot(row, ("name", "code", "facility_type", "district", "block", "is_active")),
    )
    db.session.commit()
    return row


def _master_ids() -> list:
    ids = []
    for model in (Department, Designation, EmploymentType, Qualification, TrainingType, LeaveType, Facility):
        ids.extend(db.session.scalars(select(model.id).where(model.name.like("WF %"))))
    return ids


def _delete_unreferenced_masters() -> None:
    for model in (Department, Designation, EmploymentType, Qualification, TrainingType, LeaveType, Facility):
        rows = list(db.session.scalars(select(model).where(model.name.like("WF %"))))
        for row in rows:
            if _referenced(model, row.id):
                continue
            db.session.delete(row)


def _referenced(model, record_id) -> bool:
    checks = {
        Department: (EmploymentDetail.department_id,),
        Designation: (
            JobAppointment.designation_id,
            Posting.designation_id,
            Promotion.previous_designation_id,
            Promotion.new_designation_id,
        ),
        EmploymentType: (EmploymentDetail.employment_type_id, JobAppointment.employment_type_id),
        Qualification: (EducationRecord.qualification_id,),
        TrainingType: (TrainingRecord.training_type_id,),
        LeaveType: (LeaveRecord.leave_type_id,),
        Facility: (Employee.facility_id, Posting.facility_id),
    }
    return any(
        db.session.scalar(select(column.class_.id).where(column == record_id).limit(1)) is not None
        for column in checks[model]
    )


def register_workflow_commands(app) -> None:
    @app.cli.command("seed-workflow")
    def seed_workflow():
        """Load the synthetic workflow dataset into the isolated test database."""
        _guard(app)
        message = populate_workflow_dataset(app.config["ADMIN_USER_ID"])
        print(message)
        for note in LEGACY_NOTES:
            print(note)

    @app.cli.command("cleanup-workflow")
    def cleanup_workflow():
        """Delete only the synthetic workflow dataset from the isolated test database."""
        _guard(app)
        print(remove_workflow_dataset())

    @app.cli.command("workflow-timing")
    def workflow_timing():
        """Measure in-process page work against the isolated test database.

        Authentication is not executed. These are not browser timings.
        """
        _guard(app)
        from time import perf_counter

        from flask import g
        from sqlalchemy import event

        from app.directory import employee_directory
        from app.fielddefs import ordered_fields
        from app.models import Employee
        from app.web import current_posting, get_employee

        def measure(label, callback):
            counts = []
            walls = []
            for _ in range(4):
                statements = {"n": 0}

                def count_statement(*_args, **_kwargs):
                    statements["n"] += 1

                event.listen(db.engine, "before_cursor_execute", count_statement)
                started = perf_counter()
                callback()
                walls.append(perf_counter() - started)
                counts.append(statements["n"])
                event.remove(db.engine, "before_cursor_execute", count_statement)
            later = walls[1:]
            print(
                f"{label} statements={counts[-1]} warm_median={_median(later):.3f}s "
                f"range={min(later):.3f}-{max(later):.3f}"
            )

        def run_directory(path):
            with app.test_request_context(path):
                g.actor_id = app.config["ADMIN_USER_ID"]
                employee_directory(_args_from(path))

        from app.dashboard import index as dashboard_index
        from app.employee_routes import index as employee_index
        from app.field_routes import index as field_index

        def render_register(path):
            with app.test_request_context(path):
                g.actor_id = app.config["ADMIN_USER_ID"]
                employee_index()

        def render_fields():
            with app.test_request_context("/fields/"):
                g.actor_id = app.config["ADMIN_USER_ID"]
                field_index()

        def render_dashboard():
            with app.test_request_context("/"):
                g.actor_id = app.config["ADMIN_USER_ID"]
                dashboard_index()

        print("in-process timings, authentication skipped, isolated local database")
        measure("dashboard-render", render_dashboard)
        measure("register-render", lambda: render_register("/employees/"))
        measure("search-render", lambda: render_register("/employees/?q=Shared"))
        measure("status-filter-render", lambda: render_register("/employees/?status=active"))
        measure("fields-render", render_fields)
        measure("register-query", lambda: run_directory("/employees/"))

        employee = db.session.scalar(select(Employee).where(Employee.employee_code == "WF-0011"))

        def profile_queries():
            with app.test_request_context(f"/employees/{employee.id}/personal"):
                row = get_employee(employee.id)
                current_posting(row.id)
                ordered_fields()

        if employee is not None:
            measure("profile-queries", profile_queries)

        from app.hr import update_employee
        from app.models import AuditEvent, Facility

        facility = db.session.scalar(select(Facility).where(Facility.code == "WF-NORTH"))
        started = perf_counter()
        created = create_employee(
            app.config["ADMIN_USER_ID"],
            {
                "employee_code": "WF-TIME",
                "full_name": "WF Timing Probe",
                "date_of_birth": date(1990, 1, 1),
                "gender": "female",
                "phone": "9867000999",
                "email": None,
                "facility_id": facility.id,
                "is_active": True,
                "custom_data": {},
            },
        )
        print(f"create-one elapsed={perf_counter() - started:.3f}s")
        started = perf_counter()
        update_employee(
            app.config["ADMIN_USER_ID"],
            created.id,
            created.version,
            {
                "full_name": "WF Timing Probe",
                "date_of_birth": date(1990, 1, 1),
                "gender": "female",
                "phone": "9867000998",
                "email": None,
                "facility_id": facility.id,
                "is_active": True,
                "custom_data": {},
            },
        )
        print(f"save-one elapsed={perf_counter() - started:.3f}s")
        db.session.execute(delete(AuditEvent).where(AuditEvent.entity_id == created.id))
        db.session.execute(delete(Employee).where(Employee.id == created.id))
        db.session.commit()
        print(f"dataset_employees={workflow_employee_count()}")


def _args_from(path: str):
    from werkzeug.datastructures import MultiDict
    from urllib.parse import urlsplit, parse_qs

    query = urlsplit(path).query
    return MultiDict((key, values[-1]) for key, values in parse_qs(query).items())


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _guard(app) -> None:
    import os

    assert_isolated_test_database(app.config["SQLALCHEMY_DATABASE_URI"], os.getenv("HRMS_TEST_DATABASE"))
