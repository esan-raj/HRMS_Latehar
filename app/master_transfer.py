"""Bulk download and CSV import for departments, designations, facilities, and employment types."""

import csv
import io

from sqlalchemy import select

from app.audit import column_snapshot, record_audit
from app.domain import FieldError
from app.extensions import db
from app.hr import allocate_facility_code, name_taken
from app.persistence import commit_or_conflict, new_id
from app.transfers import IMPORT_LIMIT, MAX_UPLOAD_BYTES, BulkFileError, BulkResult

SAMPLE_NAME = "Sample name"
SAMPLE_FACILITY = "Sample facility"


def master_table(spec) -> tuple[list[str], list[list[str]], bool]:
    rows = db.session.scalars(select(spec["model"]).order_by(spec["model"].name, spec["model"].id)).all()
    if spec["facility"]:
        headers = ["Code", "Name", "Type", "District", "Block", "Status"]
        data = [
            [
                row.code,
                row.name,
                row.facility_type,
                row.district,
                row.block or "",
                "Active" if row.is_active else "Inactive",
            ]
            for row in rows
        ]
    else:
        headers = ["Name", "Status"]
        data = [[row.name, "Active" if row.is_active else "Inactive"] for row in rows]
    return headers, data, False


def template_columns(spec) -> list[tuple[str, str]]:
    if spec["facility"]:
        return [
            ("name", "Required. Up to 200 characters. The facility code is assigned automatically."),
            ("facility_type", "Required. Up to 80 characters."),
            ("district", "Required. Up to 120 characters."),
            ("block", "Optional. Up to 120 characters."),
            ("status", "Optional. Use active or inactive. Blank means active."),
        ]
    return [
        ("name", "Required. Up to 120 characters."),
        ("status", "Optional. Use active or inactive. Blank means active."),
    ]


def template_csv(spec) -> bytes:
    columns = template_columns(spec)
    if spec["facility"]:
        sample = {
            "name": SAMPLE_FACILITY,
            "facility_type": "Hospital",
            "district": "Latehar",
            "block": "",
            "status": "active",
        }
    else:
        sample = {"name": SAMPLE_NAME, "status": "active"}
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for header, _hint in columns])
    writer.writerow([sample[header] for header, _hint in columns])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def import_masters(actor_id, spec, payload: bytes) -> BulkResult:
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
    headers = [name.strip() for name in reader.fieldnames if name and str(name).strip()]
    if any(name is None or not str(name).strip() for name in reader.fieldnames):
        raise BulkFileError("Use the sample template. Every column needs a heading.")
    if len(headers) != len({item.casefold() for item in headers}):
        raise BulkFileError("The template has a repeated column.")
    expected = [header for header, _hint in template_columns(spec)]
    if sorted(item.casefold() for item in headers) != sorted(item.casefold() for item in expected):
        raise BulkFileError("Use the sample template. Download it again if the columns have changed.")
    header_for = {header.casefold(): header for header in expected}
    prepared = []
    errors = []
    seen_names = set()
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
            raise BulkFileError(f"Upload at most {IMPORT_LIMIT} rows at a time.")
        if _is_sample(spec, row):
            seen -= 1
            continue
        try:
            values = _prepare_row(spec, row)
        except ValueError as exc:
            errors.append(f"Row {index}: {exc}")
            continue
        if not spec["facility"]:
            key = values["name"].casefold()
            if key in seen_names or name_taken(spec["model"], values["name"], None):
                errors.append(f"Row {index}: a record with this name already exists.")
                continue
            seen_names.add(key)
        prepared.append(values)
    if seen == 0 and not errors:
        raise BulkFileError("The file has no rows to add. Replace the sample row and upload it again.")
    if errors:
        return BulkResult(0, errors)
    for values in prepared:
        active = values.pop("is_active")
        if spec["facility"]:
            values["code"] = allocate_facility_code()
        record = spec["model"](id=new_id(), version=1, is_active=active, **values)
        db.session.add(record)
        db.session.flush()
        record_audit(actor_id, spec["entity"], record.id, "create", {}, column_snapshot(record, spec["fields"]))
    try:
        commit_or_conflict("code" if spec["facility"] else "name")
    except FieldError as exc:
        raise BulkFileError(exc.message) from exc
    return BulkResult(len(prepared), [])


def _is_sample(spec, row: dict) -> bool:
    if spec["facility"]:
        return row.get("name") == SAMPLE_FACILITY
    return row.get("name") == SAMPLE_NAME


def _prepare_row(spec, row: dict) -> dict:
    name = row.get("name", "")
    limit = 200 if spec["facility"] else 120
    if not name or len(name) > limit:
        raise ValueError(f"Enter a name of 1 to {limit} characters.")
    status = (row.get("status") or "active").casefold()
    if status not in {"active", "inactive"}:
        raise ValueError("Enter status as active or inactive.")
    if not spec["facility"]:
        return {"name": name, "is_active": status == "active"}
    facility_type = row.get("facility_type", "")
    district = row.get("district", "")
    block = row.get("block") or None
    if not facility_type or len(facility_type) > 80:
        raise ValueError("Enter a type of 1 to 80 characters.")
    if not district or len(district) > 120:
        raise ValueError("Enter a district of 1 to 120 characters.")
    if block and len(block) > 120:
        raise ValueError("Enter a block of at most 120 characters.")
    return {
        "name": name,
        "facility_type": facility_type,
        "district": district,
        "block": block,
        "is_active": status == "active",
    }
