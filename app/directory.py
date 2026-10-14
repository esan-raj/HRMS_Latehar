from sqlalchemy import Integer, Numeric, String, cast, func, or_, select
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.fielddefs import (
    FILTERABLE_TYPES,
    SORTABLE_TYPES,
    display_custom,
    ensure_system_fields,
    incomplete_employee_ids,
    json_text,
    register_fields,
)
from app.models import Designation, Employee, EmploymentDetail, Facility, Posting

PAGE_SIZE = 20
SORTS = {
    "code": Employee.employee_code,
    "name": Employee.full_name,
    "status": Employee.is_active,
    "updated": Employee.updated_at,
}


class Page:
    def __init__(self, items, page: int, pages: int, total: int):
        self.items = items
        self.page = page
        self.pages = pages
        self.total = total
        self.has_prev = page > 1
        self.has_next = page < pages


class RegisterColumn:
    def __init__(self, key: str, label: str, sortable: bool):
        self.key = key
        self.label = label
        self.sortable = sortable


class DirectoryRow:
    def __init__(self, employee, posting, employment, columns):
        self.employee = employee
        self.designation = posting.designation.name if posting is not None else None
        self.facility = posting.facility.name if posting is not None else None
        self.employment_type = employment.employment_type.name if employment is not None else None
        self.cells = []
        for column in columns:
            if column.key == "designation":
                self.cells.append(self.designation or "—")
            elif column.key == "employment_type":
                self.cells.append(self.employment_type or "—")
            else:
                self.cells.append(_cell(employee, column))


def employee_directory(args) -> tuple[Page, dict, str, str, list[RegisterColumn]]:
    ensure_system_fields()
    columns = _register_columns()
    sortable = {column.key for column in columns if column.sortable}
    query = (args.get("q") or "").strip()[:100]
    status = args.get("status") if args.get("status") in {"active", "inactive"} else ""
    requested_sort = (args.get("sort") or "name").strip()
    sort = requested_sort if requested_sort in sortable or requested_sort in SORTS else "name"
    direction = args.get("dir") if args.get("dir") in {"asc", "desc"} else "asc"
    try:
        page = int(args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)
    designation_id = _uuid(args.get("designation_id"))
    facility_id = _uuid(args.get("facility_id"))
    employment_type_id = _uuid(args.get("employment_type_id"))

    criteria = []
    if query:
        like = f"%{query}%"
        criteria.append(or_(Employee.employee_code.ilike(like), Employee.full_name.ilike(like)))
    if status == "active":
        criteria.append(Employee.is_active.is_(True))
    elif status == "inactive":
        criteria.append(Employee.is_active.is_(False))
    if designation_id is not None:
        criteria.append(
            Employee.id.in_(
                select(Posting.employee_id).where(
                    Posting.designation_id == designation_id,
                    Posting.end_date.is_(None),
                )
            )
        )
    if facility_id is not None:
        criteria.append(
            Employee.id.in_(
                select(Posting.employee_id).where(Posting.facility_id == facility_id, Posting.end_date.is_(None))
            )
        )
    if employment_type_id is not None:
        criteria.append(
            Employee.id.in_(
                select(EmploymentDetail.employee_id).where(EmploymentDetail.employment_type_id == employment_type_id)
            )
        )
    incomplete = args.get("incomplete") == "1"
    if incomplete:
        missing_ids = incomplete_employee_ids()
        criteria.append(Employee.id.in_(missing_ids or [_EMPTY]))
    custom_filter = _custom_filter(args.get("filter_field"), args.get("filter_value"))
    if custom_filter is not None:
        criteria.append(custom_filter)

    total = db.session.scalar(select(func.count()).select_from(Employee).where(*criteria)) or 0
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, pages)
    ordering = _ordering(sort, direction)
    employees = db.session.scalars(
        select(Employee)
        .options(joinedload(Employee.facility))
        .where(*criteria)
        .order_by(ordering, Employee.id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).unique().all()
    postings = {}
    employments = {}
    ids = [employee.id for employee in employees]
    if ids:
        posting_rows = db.session.scalars(
            select(Posting)
            .options(joinedload(Posting.designation), joinedload(Posting.facility))
            .where(Posting.employee_id.in_(ids), Posting.end_date.is_(None))
        ).unique()
        for posting in posting_rows:
            postings[posting.employee_id] = posting
        detail_rows = db.session.scalars(
            select(EmploymentDetail)
            .options(joinedload(EmploymentDetail.employment_type))
            .where(EmploymentDetail.employee_id.in_(ids))
        ).unique()
        for detail in detail_rows:
            employments[detail.employee_id] = detail
    visible = [column for column in columns if column.key not in {"code", "status"}]
    items = [
        DirectoryRow(employee, postings.get(employee.id), employments.get(employee.id), visible)
        for employee in employees
    ]
    filters = {}
    if query:
        filters["q"] = query
    if status:
        filters["status"] = status
    if designation_id is not None:
        filters["designation_id"] = str(designation_id)
    if facility_id is not None:
        filters["facility_id"] = str(facility_id)
    if employment_type_id is not None:
        filters["employment_type_id"] = str(employment_type_id)
    if incomplete:
        filters["incomplete"] = "1"
    if args.get("filter_field") and args.get("filter_value"):
        filters["filter_field"] = str(args.get("filter_field"))[:40]
        filters["filter_value"] = str(args.get("filter_value"))[:100]
    return Page(items, page, pages, total), filters, sort, direction, columns


def dashboard_counts() -> dict:
    total = db.session.scalar(select(func.count()).select_from(Employee)) or 0
    active = db.session.scalar(select(func.count()).select_from(Employee).where(Employee.is_active.is_(True))) or 0
    inactive = db.session.scalar(select(func.count()).select_from(Employee).where(Employee.is_active.is_(False))) or 0
    by_designation = db.session.execute(
        select(Designation.name, func.count(Employee.id))
        .join(Posting, Posting.designation_id == Designation.id)
        .join(Employee, Employee.id == Posting.employee_id)
        .where(Posting.end_date.is_(None), Employee.is_active.is_(True))
        .group_by(Designation.name)
        .order_by(Designation.name)
    ).all()
    by_facility = db.session.execute(
        select(Facility.name, func.count(Employee.id))
        .join(Posting, Posting.facility_id == Facility.id)
        .join(Employee, Employee.id == Posting.employee_id)
        .where(Posting.end_date.is_(None), Employee.is_active.is_(True))
        .group_by(Facility.name)
        .order_by(Facility.name)
    ).all()
    assigned = select(Posting.employee_id).where(Posting.end_date.is_(None))
    unassigned = (
        db.session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(Employee.is_active.is_(True), Employee.id.not_in(assigned))
        )
        or 0
    )
    return {
        "total": total,
        "active": active,
        "inactive": inactive,
        "by_designation": by_designation,
        "by_facility": by_facility,
        "unassigned": unassigned,
    }


_EMPTY = __import__("uuid").UUID(int=0)
_SYSTEM_SORT = {"full_name": "name", "phone": "phone", "facility_id": "facility"}


def _register_columns() -> list[RegisterColumn]:
    columns = [RegisterColumn("code", "Code", True)]
    for field in register_fields():
        key = _SYSTEM_SORT.get(field.field_key, str(field.id))
        sortable = field.field_type in SORTABLE_TYPES or field.field_key in _SYSTEM_SORT
        columns.append(RegisterColumn(key, field.label, sortable))
    columns.append(RegisterColumn("designation", "Designation", False))
    columns.append(RegisterColumn("employment_type", "Employment type", False))
    columns.append(RegisterColumn("status", "Status", True))
    return columns


def _ordering(sort: str, direction: str):
    if sort == "facility":
        expression = select(Facility.name).where(Facility.id == Employee.facility_id).scalar_subquery()
    elif sort == "phone":
        expression = Employee.phone
    elif sort in SORTS:
        expression = SORTS[sort]
    else:
        field = _custom_field(sort)
        if field is None or field.field_type not in SORTABLE_TYPES:
            expression = Employee.full_name
        else:
            value = json_text(str(field.id), "sort")
            if field.field_type == "integer":
                expression = cast(value, Integer)
            elif field.field_type == "decimal":
                expression = cast(value, Numeric(14, 4))
            else:
                expression = value
    return expression.desc() if direction == "desc" else expression.asc()


def _custom_filter(field_id, raw):
    import uuid

    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = uuid.UUID(str(field_id))
    except (TypeError, ValueError):
        return None
    from app.models import EmployeeField

    field = db.session.get(EmployeeField, parsed)
    if field is None or field.is_archived or field.is_system or field.field_type not in FILTERABLE_TYPES:
        return None
    if field.field_type == "integer" and not text.lstrip("+-").isdigit():
        return Employee.id.is_(None)
    if field.field_type == "yes_no" and text not in {"yes", "no"}:
        return Employee.id.is_(None)
    if field.field_type == "dropdown":
        try:
            uuid.UUID(text)
        except ValueError:
            return Employee.id.is_(None)
    expression = cast(json_text(str(field.id), "filter"), String)
    if field.field_type == "short_text":
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return expression.ilike(f"%{escaped}%", escape="\\")
    return expression == text


def _custom_field(sort: str):
    import uuid

    from app.models import EmployeeField

    try:
        parsed = uuid.UUID(sort)
    except (TypeError, ValueError):
        return None
    return db.session.get(EmployeeField, parsed)


def _cell(employee, column: RegisterColumn) -> str:
    if column.key == "name":
        return employee.full_name
    if column.key == "phone":
        return employee.phone or "—"
    if column.key == "facility":
        return employee.facility.name if employee.facility is not None else "—"
    field = _custom_field(column.key)
    if field is None:
        return "—"
    return display_custom(field, (employee.custom_data or {}).get(str(field.id)))


def _uuid(value):
    import uuid

    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
