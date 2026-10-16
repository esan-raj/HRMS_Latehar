"""Import doctor names from the Latehar OPD workbook.

Mobile number, date of birth, and gender are not in that sheet, so they are
left empty. The personal form still requires them when the record is saved.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

from sqlalchemy import func, select

from app.audit import column_snapshot, record_audit
from app.extensions import db
from app.hr import EMPLOYEE_FIELDS, allocate_employee_code, allocate_facility_code
from app.models import Designation, Employee, EmploymentType, Facility
from app.persistence import new_id

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _shared_strings(payload: bytes) -> list[str]:
    root = ET.fromstring(payload)
    values = []
    for item in root.findall("m:si", NS):
        values.append("".join(node.text or "" for node in item.iter(f"{{{NS['m']}}}t")))
    return values


def assert_production_database(database_url: str) -> str:
    """Return the host. Refuse the isolated test database and any local host."""
    parts = urlsplit((database_url or "").replace("postgresql+psycopg://", "postgresql://", 1))
    host = (parts.hostname or "").lower()
    name = parts.path.strip("/").split("/")[-1]
    if host in {"", "localhost", "127.0.0.1"} or name == "hrms_test":
        raise SystemExit("Refusing roster import: this is the local test database, not the HR database.")
    if "supabase" not in host:
        raise SystemExit(f"Refusing roster import: unrecognized database host {host or '(none)'}.")
    return host


def read_opd_rows(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as book:
        strings = _shared_strings(book.read("xl/sharedStrings.xml"))
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.findall("m:sheetData/m:row", NS)[1:]:
        values = {}
        for cell in row.findall("m:c", NS):
            column = "".join(character for character in cell.attrib.get("r", "") if character.isalpha())
            raw = cell.find("m:v", NS)
            text = "" if raw is None or raw.text is None else raw.text
            if cell.attrib.get("t") == "s" and text.isdigit():
                text = strings[int(text)]
            values[column] = str(text).strip()
        name = re.sub(r"\s+", " ", values.get("B", "")).strip()
        facility = re.sub(r"\s+", " ", values.get("C", "")).strip()
        if not name or not facility:
            continue
        rows.append(
            {
                "name": name,
                "facility": facility,
                "post": re.sub(r"\s+", " ", values.get("D", "")).strip(),
                "employment_type": re.sub(r"\s+", " ", values.get("E", "")).strip(),
            }
        )
    return rows


def import_opd_roster(actor_id, path: Path) -> str:
    rows = read_opd_rows(path)
    created = 0
    skipped = 0
    for row in rows:
        facility = _facility(actor_id, row["facility"])
        if row["post"]:
            _named(actor_id, Designation, "designation", row["post"])
        if row["employment_type"]:
            _named(actor_id, EmploymentType, "employment_type", row["employment_type"])
        existing = db.session.scalar(
            select(Employee.id).where(
                func.lower(Employee.full_name) == row["name"].lower(),
                Employee.facility_id == facility.id,
            )
        )
        if existing is not None:
            skipped += 1
            continue
        employee = Employee(
            id=new_id(),
            version=1,
            employee_code=allocate_employee_code(),
            full_name=row["name"],
            date_of_birth=None,
            gender=None,
            phone=None,
            email=None,
            facility_id=facility.id,
            custom_data={},
            is_active=True,
        )
        db.session.add(employee)
        record_audit(actor_id, "employee", employee.id, "create", {}, column_snapshot(employee, EMPLOYEE_FIELDS))
        db.session.commit()
        created += 1
    return f"Roster import created {created} employees and skipped {skipped} already present."


def _facility(actor_id, name: str) -> Facility:
    existing = db.session.scalar(select(Facility).where(func.lower(Facility.name) == name.lower()))
    if existing is not None:
        return existing
    if name.lower() == "sadar hospital, latehar":
        sadar = db.session.scalar(select(Facility).where(func.lower(Facility.name).like("sadar hospital%")))
        if sadar is not None:
            return sadar
    prefix = "Hospital"
    lowered = name.lower()
    if lowered.startswith("phc"):
        prefix = "PHC"
    elif lowered.startswith("chc"):
        prefix = "CHC"
    row = Facility(
        id=new_id(),
        version=1,
        name=name,
        code=allocate_facility_code(),
        facility_type=prefix,
        district="Latehar",
        block=None,
        is_active=True,
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


def _named(actor_id, model, entity, name):
    existing = db.session.scalar(select(model).where(func.lower(model.name) == name.lower()))
    if existing is not None:
        return existing
    row = model(id=new_id(), version=1, name=name, is_active=True)
    db.session.add(row)
    record_audit(actor_id, entity, row.id, "create", {}, column_snapshot(row, ("name", "is_active")))
    db.session.commit()
    return row


def register_roster_command(app) -> None:
    import click

    @app.cli.command("import-opd-roster")
    @click.argument("workbook", required=False)
    def import_opd_roster_command(workbook):
        """Add doctor names from the OPD workbook to the HR database."""
        import os

        from dotenv import load_dotenv

        load_dotenv(override=True)
        os.environ.pop("HRMS_TEST_DATABASE", None)
        configured = app.config["SQLALCHEMY_DATABASE_URI"]
        fresh = os.environ.get("DATABASE_URL", "")
        if "127.0.0.1" in configured or "localhost" in configured or "hrms_test" in configured:
            raise SystemExit("This process is using the local test database. Unset DATABASE_URL and run the command again.")
        host = assert_production_database(configured)
        fresh_host = urlsplit(fresh.replace("postgresql+psycopg://", "postgresql://", 1)).hostname or ""
        if fresh_host and fresh_host.lower() != host:
            raise SystemExit("DATABASE_URL does not match the application database. Unset DATABASE_URL and run the command again.")
        path = Path(workbook) if workbook else Path(__file__).resolve().parent.parent / "OPD Reporting September.xlsx"
        print(f"database_host={host}")
        print(import_opd_roster(app.config["ADMIN_USER_ID"], path))
