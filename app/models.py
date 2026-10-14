import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
    false as sa_false,
    true as sa_true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.audit import utcnow
from app.extensions import db


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class VersionMixin:
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class NamedMaster(UUIDPrimaryKey, TimestampMixin, VersionMixin):
    __abstract__ = True
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_true())


class Department(NamedMaster, db.Model):
    __tablename__ = "departments"


class Designation(NamedMaster, db.Model):
    __tablename__ = "designations"


class EmploymentType(NamedMaster, db.Model):
    __tablename__ = "employment_types"


class Qualification(NamedMaster, db.Model):
    __tablename__ = "qualifications"


class TrainingType(NamedMaster, db.Model):
    __tablename__ = "training_types"


class LeaveType(NamedMaster, db.Model):
    __tablename__ = "leave_types"


class Facility(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "facilities"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    facility_type: Mapped[str] = mapped_column(String(80), nullable=False)
    district: Mapped[str] = mapped_column(String(120), nullable=False)
    block: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_true())


class Employee(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "employees"

    employee_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    date_of_birth: Mapped[datetime] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(16), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("facilities.id", ondelete="RESTRICT"), index=True
    )
    custom_data: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sa_true(), index=True)

    facility: Mapped["Facility | None"] = relationship()
    addresses: Mapped[list["Address"]] = relationship(back_populates="employee")
    employment_detail: Mapped["EmploymentDetail | None"] = relationship(back_populates="employee", uselist=False)


class Address(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "addresses"
    __table_args__ = (
        CheckConstraint("address_type IN ('present', 'permanent')", name="ck_addresses_type"),
        CheckConstraint("pin_code IS NULL OR length(pin_code) BETWEEN 4 AND 10", name="ck_addresses_pin"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    address_type: Mapped[str] = mapped_column(String(20), nullable=False)
    line1: Mapped[str] = mapped_column(String(250), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(250))
    district: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    pin_code: Mapped[str] = mapped_column(String(10), nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="addresses")


class EducationRecord(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "education_records"
    __table_args__ = (CheckConstraint("passing_year BETWEEN 1950 AND 2100", name="ck_education_records_year"),)

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    qualification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("qualifications.id", ondelete="RESTRICT"), nullable=False, index=True)
    specialization: Mapped[str | None] = mapped_column(String(200))
    institution: Mapped[str] = mapped_column(String(250), nullable=False)
    passing_year: Mapped[int] = mapped_column(Integer, nullable=False)

    qualification: Mapped[Qualification] = relationship()


class TrainingRecord(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "training_records"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="ck_training_records_dates"),)

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    training_type_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("training_types.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    provider: Mapped[str] = mapped_column(String(250), nullable=False)
    start_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    training_type: Mapped[TrainingType | None] = relationship()


class JobAppointment(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "job_appointments"
    __table_args__ = (CheckConstraint("joining_date >= appointment_date", name="ck_job_appointments_dates"),)

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    appointment_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    appointment_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    joining_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    designation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("designations.id", ondelete="RESTRICT"), nullable=False, index=True)
    employment_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employment_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    designation: Mapped[Designation] = relationship()
    employment_type: Mapped[EmploymentType] = relationship()


class EmploymentDetail(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "employment_details"
    __table_args__ = (
        CheckConstraint(
            "retirement_date IS NULL OR retirement_date >= service_joining_date",
            name="ck_employment_details_dates",
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True)
    employment_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employment_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_joining_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    retirement_date: Mapped[datetime | None] = mapped_column(Date)
    pay_level: Mapped[str] = mapped_column(String(50), nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="employment_detail")
    department: Mapped[Department] = relationship()
    employment_type: Mapped[EmploymentType] = relationship()


class Posting(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "postings"
    __table_args__ = (CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_postings_dates"),)

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    facility_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False, index=True)
    designation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("designations.id", ondelete="RESTRICT"), nullable=False, index=True)
    start_date: Mapped[datetime] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[datetime | None] = mapped_column(Date)
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    facility: Mapped[Facility] = relationship()
    designation: Mapped[Designation] = relationship()


class Promotion(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "promotions"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    previous_designation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("designations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    new_designation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("designations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    effective_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    previous_designation: Mapped[Designation] = relationship(foreign_keys=[previous_designation_id])
    new_designation: Mapped[Designation] = relationship(foreign_keys=[new_designation_id])


class Allegation(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "allegations"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_allegations_status"),
        CheckConstraint("resolution_date IS NULL OR resolution_date >= recorded_date", name="ck_allegations_dates"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    reference_no: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resolution_date: Mapped[datetime | None] = mapped_column(Date)
    remarks: Mapped[str | None] = mapped_column(Text)


class LeaveRecord(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "leave_records"
    __table_args__ = (
        CheckConstraint("status IN ('recorded', 'cancelled')", name="ck_leave_records_status"),
        CheckConstraint("end_date >= start_date", name="ck_leave_records_dates"),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    leave_type_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leave_types.id", ondelete="RESTRICT"), nullable=False, index=True)
    start_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)

    leave_type: Mapped[LeaveType] = relationship()


class DacpDetail(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "dacp_details"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(80), nullable=False)
    effective_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    remarks: Mapped[str | None] = mapped_column(Text)


class FieldCatalog(db.Model):
    __tablename__ = "field_catalog"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class EmployeeField(UUIDPrimaryKey, TimestampMixin, VersionMixin, db.Model):
    __tablename__ = "employee_fields"
    __table_args__ = (
        CheckConstraint(
            "field_type IN ('short_text', 'long_text', 'integer', 'decimal', 'date', 'dropdown', 'yes_no', 'facility')",
            name="ck_employee_fields_type",
        ),
    )

    field_key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    help_text: Mapped[str | None] = mapped_column(String(300))
    placeholder: Mapped[str | None] = mapped_column(String(120))
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    section: Mapped[str] = mapped_column(String(80), nullable=False, default="Personal Details")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    show_in_register: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())

    options: Mapped[list["EmployeeFieldOption"]] = relationship(
        back_populates="field",
        order_by="EmployeeFieldOption.sort_order",
        cascade="all, delete-orphan",
    )


class EmployeeFieldOption(UUIDPrimaryKey, TimestampMixin, db.Model):
    __tablename__ = "employee_field_options"

    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employee_fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_retired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_false())

    field: Mapped[EmployeeField] = relationship(back_populates="options")


class AuditEvent(UUIDPrimaryKey, db.Model):
    __tablename__ = "audit_events"

    actor_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    changes: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class LoginThrottle(db.Model):
    __tablename__ = "login_throttle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


Index("uq_departments_name_ci", func.lower(Department.name), unique=True)
Index("uq_designations_name_ci", func.lower(Designation.name), unique=True)
Index("uq_employment_types_name_ci", func.lower(EmploymentType.name), unique=True)
Index("uq_qualifications_name_ci", func.lower(Qualification.name), unique=True)
Index("uq_training_types_name_ci", func.lower(TrainingType.name), unique=True)
Index("uq_leave_types_name_ci", func.lower(LeaveType.name), unique=True)
Index(
    "uq_addresses_employee_type",
    Address.employee_id,
    Address.address_type,
    unique=True,
)
Index("ix_audit_events_entity", AuditEvent.entity_type, AuditEvent.entity_id)
Index(
    "uq_postings_one_open",
    Posting.employee_id,
    unique=True,
    sqlite_where=text("end_date IS NULL"),
    postgresql_where=text("end_date IS NULL"),
)
