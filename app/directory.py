from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.extensions import db
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


class DirectoryRow:
    def __init__(self, employee, posting, employment):
        self.employee = employee
        self.designation = posting.designation.name if posting is not None else None
        self.facility = posting.facility.name if posting is not None else None
        self.employment_type = employment.employment_type.name if employment is not None else None


def employee_directory(args) -> tuple[Page, dict, str, str]:
    query = (args.get("q") or "").strip()[:100]
    status = args.get("status") if args.get("status") in {"active", "inactive"} else ""
    sort = args.get("sort") if args.get("sort") in SORTS else "name"
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

    total = db.session.scalar(select(func.count()).select_from(Employee).where(*criteria)) or 0
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, pages)
    column = SORTS[sort]
    ordering = column.desc() if direction == "desc" else column.asc()
    employees = db.session.scalars(
        select(Employee).where(*criteria).order_by(ordering, Employee.id).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    ).all()
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
    items = [DirectoryRow(employee, postings.get(employee.id), employments.get(employee.id)) for employee in employees]
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
    return Page(items, page, pages, total), filters, sort, direction


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


def _uuid(value):
    import uuid

    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
