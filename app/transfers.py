"""Read-only employee records, spreadsheet and PDF downloads, and CSV import.

The sample CSV is the only accepted shape. Employee codes are assigned here.
Mobile numbers, dates of birth, and gender are taken from the file and are
never invented.
"""

import csv
import io
import re
from datetime import date

from email_validator import EmailNotValidError, validate_email
from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.audit import column_snapshot, record_audit
from app.directory import matching_employees
from app.extensions import db
from app.fielddefs import active_custom_fields, display_custom, is_aadhaar_field, load_dropdown_options
from app.fielddefs import _parse_value
from app.forms import phone_problem
from app.hr import EMPLOYEE_FIELDS, allocate_employee_code
from app.models import Employee, EmploymentDetail, Facility, Posting
from app.persistence import new_id

EXPORT_LIMIT = 2000
IMPORT_LIMIT = 200
MAX_UPLOAD_BYTES = 1_000_000
FIXED_HEADERS = (
    "full_name",
    "date_of_birth",
    "gender",
    "phone",
    "facility",
    "email",
    "status",
)
SAMPLE_NAME = "Dr. Sample Name"
SAMPLE_PHONE = "9867000001"
GENDERS = {"female": "Female", "male": "Male", "other": "Other"}
_FORMULA = ("=", "+", "-", "@")


class BulkFileError(Exception):
    def __init__(self, message: str):
        self.message = message


class BulkResult:
    def __init__(self, created: int, errors: list[str]):
        self.created = created
        self.errors = errors


def record_facts(employee) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]] | None]:
    facility = employee.facility.name if employee.facility is not None else "—"
    personal = [
        ("Date of birth", _show(employee.date_of_birth)),
        ("Gender", GENDERS.get(employee.gender or "", "—")),
        ("Mobile number", employee.phone or "—"),
        ("Email", employee.email or "—"),
        ("Facility", facility),
    ]
    fields = active_custom_fields()
    load_dropdown_options([field for field in fields if field.field_type == "dropdown"])
    custom = [
        (field.label, display_custom(field, (employee.custom_data or {}).get(str(field.id))))
        for field in fields
    ]
    detail = db.session.scalar(
        select(EmploymentDetail)
        .options(joinedload(EmploymentDetail.department), joinedload(EmploymentDetail.employment_type))
        .where(EmploymentDetail.employee_id == employee.id)
    )
    employment = None
    if detail is not None:
        employment = [
            ("Department", detail.department.name),
            ("Employment type", detail.employment_type.name),
            ("Service joining date", _show(detail.service_joining_date)),
            ("Retirement date", _show(detail.retirement_date)),
            ("Pay level", detail.pay_level),
        ]
    return personal, custom, employment


def export_table(args) -> tuple[list[str], list[list[str]], bool]:
    employees = matching_employees(args, EXPORT_LIMIT)
    truncated = len(employees) > EXPORT_LIMIT
    employees = employees[:EXPORT_LIMIT]
    fields = active_custom_fields()
    load_dropdown_options([field for field in fields if field.field_type == "dropdown"])
    postings, employments = _related(employees)
    headers = [
        "Employee code",
        "Name",
        "Date of birth",
        "Gender",
        "Mobile number",
        "Email",
        "Facility",
        "Designation",
        "Employment type",
        "Status",
    ]
    headers.extend(field.label for field in fields)
    rows = []
    for employee in employees:
        posting = postings.get(employee.id)
        detail = employments.get(employee.id)
        values = [
            employee.employee_code,
            employee.full_name,
            employee.date_of_birth.isoformat() if employee.date_of_birth else "",
            GENDERS.get(employee.gender or "", ""),
            employee.phone or "",
            employee.email or "",
            employee.facility.name if employee.facility is not None else "",
            posting.designation.name if posting is not None else "",
            detail.employment_type.name if detail is not None else "",
            "Active" if employee.is_active else "Inactive",
        ]
        stored = employee.custom_data or {}
        values.extend(display_custom(field, stored.get(str(field.id))).replace("—", "") for field in fields)
        rows.append(values)
    return headers, rows, truncated


def workbook_bytes(headers: list[str], rows: list[list[str]], truncated: bool, title: str = "Employees") -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = (title or "Records")[:31]
    if truncated:
        sheet.append([f"This file contains the first {EXPORT_LIMIT} matching rows."])
    sheet.append(headers)
    for row in rows:
        cells = [_sheet_text(value) for value in row]
        sheet.append(cells)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell, Cell) and isinstance(cell.value, str) and cell.value[:1] in _FORMULA:
                cell.value = "'" + cell.value
                cell.data_type = "s"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def pdf_bytes(headers: list[str], rows: list[list[str]], truncated: bool, title: str = "employees") -> bytes:
    lines = ["HR Records", f"{len(rows)} {title}"]
    if truncated:
        lines.append(f"This file contains the first {EXPORT_LIMIT} matching rows.")
    if not rows:
        lines.append(f"No {title} to download.")
    for row in rows:
        lines.append("")
        for header, value in zip(headers, row):
            lines.extend(_wrap(f"{header}: {value or '—'}"))
    return _pdf_document(lines)


def template_columns() -> list[tuple[object | None, str, str]]:
    """Return fixed and custom columns as (field or None, header, hint)."""
    columns = [
        (None, "full_name", "Required. The employee's name."),
        (None, "date_of_birth", "Required. Use YYYY-MM-DD."),
        (None, "gender", "Required. Use female, male, or other."),
        (None, "phone", "Required. 10 digits starting with 6, 7, 8, or 9."),
        (None, "facility", "Required. Use a facility name that already exists."),
        (None, "email", "Optional."),
        (None, "status", "Optional. Use active or inactive. Blank means active."),
    ]
    used = {header.casefold() for _field, header, _hint in columns}
    fields = active_custom_fields()
    load_dropdown_options([field for field in fields if field.field_type == "dropdown"])
    for field in fields:
        header = field.label.strip()
        if header.casefold() in used:
            header = f"{header} ({field.section})"
        used.add(header.casefold())
        columns.append((field, header, _custom_hint(field)))
    return columns


def template_csv() -> bytes:
    columns = template_columns()
    sample = {
        "full_name": SAMPLE_NAME,
        "date_of_birth": "1990-01-15",
        "gender": "female",
        "phone": SAMPLE_PHONE,
        "facility": "Exact facility name",
        "email": "sample@example.com",
        "status": "active",
    }
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for _field, header, _hint in columns])
    writer.writerow([sample.get(header, "") for _field, header, _hint in columns])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def import_employees(actor_id, payload: bytes) -> BulkResult:
    if len(payload) > MAX_UPLOAD_BYTES:
        raise BulkFileError("The file is larger than 1 MB.")
    if payload.startswith(b"PK"):
        raise BulkFileError("Upload the sample template as a CSV file.")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeError as exc:
        raise BulkFileError("Save the file as CSV UTF-8.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BulkFileError("The file is empty.")
    headers = [name.strip() for name in reader.fieldnames if name and name.strip()]
    if any(name is None or not str(name).strip() for name in reader.fieldnames):
        raise BulkFileError("Use the sample template. Every column needs a heading.")
    if len(headers) != len(set(item.casefold() for item in headers)):
        raise BulkFileError("The template has a repeated column.")
    columns = template_columns()
    expected = [header for _field, header, _hint in columns]
    if sorted(item.casefold() for item in headers) != sorted(item.casefold() for item in expected):
        raise BulkFileError("Use the sample template. Download it again if the columns have changed.")
    header_for = {header.casefold(): header for _field, header, _hint in columns}
    prepared = []
    errors = []
    seen = 0
    for index, raw in enumerate(reader, start=2):
        if raw.get(None):
            errors.append(f"Row {index}: remove extra values that do not have a column.")
            continue
        row = {header_for[key.strip().casefold()]: (value or "").strip() for key, value in raw.items() if key}
        if not any(row.values()):
            continue
        seen += 1
        if seen > IMPORT_LIMIT:
            raise BulkFileError(f"Upload at most {IMPORT_LIMIT} employees at a time.")
        if row.get("full_name") == SAMPLE_NAME and row.get("phone") == SAMPLE_PHONE:
            seen -= 1
            continue
        try:
            prepared.append(_prepare_row(row, columns))
        except ValueError as exc:
            errors.append(f"Row {index}: {exc}")
    if seen == 0 and not errors:
        raise BulkFileError("The file has no employee rows. Replace the sample row and upload it again.")
    if errors:
        return BulkResult(0, errors)
    for values in prepared:
        employee = _insert_employee(actor_id, values)
        record_audit(actor_id, "employee", employee.id, "create", {}, column_snapshot(employee, EMPLOYEE_FIELDS))
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise BulkFileError("Those rows could not be saved. Upload the file again.") from exc
    return BulkResult(len(prepared), [])


def _prepare_row(row: dict, columns) -> dict:
    name = row.get("full_name", "")
    if len(name) < 2 or len(name) > 200 or not any(character.isalpha() for character in name):
        raise ValueError("Enter a name of 2 to 200 characters, including letters.")
    born = _birth_date(row.get("date_of_birth", ""))
    if born > date.today() or born < date(1900, 1, 1):
        raise ValueError("Enter a date of birth between 1900 and today.")
    gender = row.get("gender", "").casefold()
    if gender not in GENDERS:
        raise ValueError("Enter gender as female, male, or other.")
    phone = re.sub(r"\s+", "", row.get("phone", ""))
    if re.fullmatch(r"[0-9]+\.0", phone):
        phone = phone[:-2]
    message = phone_problem(phone)
    if message:
        raise ValueError(message)
    facility = _facility(row.get("facility", ""))
    email = row.get("email") or None
    if email:
        if len(email) > 254:
            raise ValueError("Enter an email address of at most 254 characters.")
        try:
            email = validate_email(email, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError("Enter a valid email address.") from exc
    status = (row.get("status") or "active").casefold()
    if status not in {"active", "inactive"}:
        raise ValueError("Enter status as active or inactive.")
    custom = {}
    for field, header, _hint in columns:
        if field is None:
            continue
        raw = row.get(header, "")
        if field.field_type == "dropdown" and raw:
            raw = _dropdown_choice(field, raw)
        elif field.field_type == "yes_no" and raw:
            raw = _yes_no(raw)
        try:
            parsed = _parse_value(field, raw, None)
        except ValueError as exc:
            raise ValueError(f"{header}: {exc}") from exc
        if parsed is not None:
            custom[str(field.id)] = parsed
    return {
        "full_name": name,
        "date_of_birth": born,
        "gender": gender,
        "phone": phone,
        "email": email,
        "facility_id": facility.id,
        "custom_data": custom,
        "is_active": status == "active",
    }


def _insert_employee(actor_id, values: dict):
    del actor_id
    employee = Employee(
        id=new_id(),
        version=1,
        employee_code=allocate_employee_code(),
        full_name=values["full_name"],
        date_of_birth=values["date_of_birth"],
        gender=values["gender"],
        phone=values["phone"],
        email=values["email"],
        facility_id=values["facility_id"],
        custom_data=values["custom_data"],
        is_active=values["is_active"],
    )
    db.session.add(employee)
    db.session.flush()
    return employee


def _birth_date(value: str) -> date:
    from datetime import datetime

    text = value.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError("Enter a date of birth as YYYY-MM-DD.")


def _facility(name: str) -> Facility:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Enter a facility name.")
    escaped = cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    matches = db.session.scalars(select(Facility).where(Facility.name.ilike(escaped, escape="\\"))).all()
    exact = [row for row in matches if row.name.casefold() == cleaned.casefold()]
    if len(exact) > 1:
        raise ValueError("More than one facility uses that name.")
    if not exact:
        raise ValueError("Enter a facility name that already exists.")
    if not exact[0].is_active:
        raise ValueError("That facility is inactive.")
    return exact[0]


def _dropdown_choice(field, raw: str) -> str:
    matches = [option for option in field.options if option.label.casefold() == raw.casefold() and not option.is_retired]
    if len(matches) != 1:
        choices = ", ".join(option.label for option in field.options if not option.is_retired)
        raise ValueError(f"{field.label}: choose one of {choices}.")
    return str(matches[0].id)


def _yes_no(raw: str) -> str:
    text = raw.casefold()
    if text in {"yes", "y", "true"}:
        return "yes"
    if text in {"no", "n", "false"}:
        return "no"
    raise ValueError("Choose yes or no.")


def _custom_hint(field) -> str:
    required = "Required. " if field.required else "Optional. "
    if is_aadhaar_field(field):
        return required + "12 digits. Spaces are allowed."
    if field.field_type == "dropdown":
        choices = ", ".join(option.label for option in field.options if not option.is_retired)
        return required + f"Choose one of: {choices}."
    if field.field_type == "yes_no":
        return required + "Use yes or no."
    if field.field_type == "date":
        return required + "Use YYYY-MM-DD."
    if field.field_type == "integer":
        return required + "Use a whole number."
    if field.field_type == "decimal":
        return required + "Use a number."
    return required + "Text."


def _related(employees):
    postings = {}
    employments = {}
    ids = [employee.id for employee in employees]
    if not ids:
        return postings, employments
    for posting in db.session.scalars(
        select(Posting)
        .options(joinedload(Posting.designation))
        .where(Posting.employee_id.in_(ids), Posting.end_date.is_(None))
    ).unique():
        postings[posting.employee_id] = posting
    for detail in db.session.scalars(
        select(EmploymentDetail)
        .options(joinedload(EmploymentDetail.employment_type))
        .where(EmploymentDetail.employee_id.in_(ids))
    ).unique():
        employments[detail.employee_id] = detail
    return postings, employments


def _show(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d %b %Y")


def _sheet_text(value) -> str:
    return "" if value is None else str(value)


def _wrap(value: str, width: int = 90) -> list[str]:
    text = _pdf_text(value)
    if not text:
        return [""]
    lines = []
    while len(text) > width:
        split = text.rfind(" ", 0, width)
        if split < 1:
            split = width
        lines.append(text[:split])
        text = text[split:].lstrip()
    lines.append(text)
    return lines


def _pdf_text(value: str) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\x00", "")
    return text.encode("latin-1", "replace").decode("latin-1")


def _pdf_document(lines: list[str]) -> bytes:
    """A text PDF using the standard Helvetica font. No extra packages."""
    page_size = 48
    chunks = [lines[index : index + page_size] for index in range(0, len(lines), page_size)] or [[]]
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    content_ids = []
    for chunk in chunks:
        commands = ["BT", "/F1 11 Tf", "14 TL", "48 800 Td"]
        for line in chunk:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) '")
        commands.append("ET")
        content = "\n".join(commands).encode("latin-1")
        content_ids.append(add(b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream"))
    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add(b"")
    built_pages = []
    for content_id in content_ids:
        built_pages.append(
            add(
                b"<< /Type /Page /Parent "
                + str(pages_id).encode("ascii")
                + b" 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 "
                + str(font_id).encode("ascii")
                + b" 0 R >> >> /Contents "
                + str(content_id).encode("ascii")
                + b" 0 R >>"
            )
        )
    kids = b" ".join(f"{page_id} 0 R".encode("ascii") for page_id in built_pages)
    objects[pages_id - 1] = b"<< /Type /Pages /Count " + str(len(built_pages)).encode("ascii") + b" /Kids [" + kids + b"] >>"
    catalog_id = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode("ascii") + b" 0 R >>")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii")
    )
    return bytes(output)
