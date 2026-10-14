"""Add employee facility, custom values, and field definitions.

Revision ID: 0002_employee_fields
Revises: 0001_initial
Create Date: 2026-10-14

Existing employees are kept. A missing mobile number stays missing. Facility is
copied only from an open posting when the employee does not already have one.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_employee_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "employees" in tables:
        columns = {column["name"] for column in inspector.get_columns("employees")}
        if "facility_id" not in columns:
            op.add_column("employees", sa.Column("facility_id", sa.Uuid(), nullable=True))
            op.create_index("ix_employees_facility_id", "employees", ["facility_id"])
            op.create_foreign_key(
                "fk_employees_facility_id",
                "employees",
                "facilities",
                ["facility_id"],
                ["id"],
                ondelete="RESTRICT",
            )
        if "custom_data" not in columns:
            op.add_column(
                "employees",
                sa.Column("custom_data", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            )
    from app.models import EmployeeField, EmployeeFieldOption, FieldCatalog

    for table in (FieldCatalog.__table__, EmployeeField.__table__, EmployeeFieldOption.__table__):
        table.create(bind=bind, checkfirst=True)
    from sqlalchemy.orm import Session

    from app.fielddefs import backfill_employee_facilities, ensure_system_fields

    session = Session(bind=bind)
    ensure_system_fields(session, commit=False)
    backfill_employee_facilities(session)
    session.flush()
    for name in ("field_catalog", "employee_fields", "employee_field_options"):
        if not name.replace("_", "").isalnum():
            raise RuntimeError("Refusing to lock down an unexpected table name.")
        op.execute(f"ALTER TABLE IF EXISTS hrms.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hrms_app') THEN
                    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON hrms.{name} TO hrms_app';
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies
                        WHERE schemaname = 'hrms' AND tablename = '{name}' AND policyname = 'hrms_app_all'
                    ) THEN
                        EXECUTE 'CREATE POLICY hrms_app_all ON hrms.{name} FOR ALL TO hrms_app USING (true) WITH CHECK (true)';
                    END IF;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    EXECUTE 'REVOKE ALL ON hrms.{name} FROM anon';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    EXECUTE 'REVOKE ALL ON hrms.{name} FROM authenticated';
                END IF;
            END $$;
            """
        )


def downgrade():
    bind = op.get_bind()
    from app.models import EmployeeField, EmployeeFieldOption, FieldCatalog

    EmployeeFieldOption.__table__.drop(bind=bind, checkfirst=True)
    EmployeeField.__table__.drop(bind=bind, checkfirst=True)
    FieldCatalog.__table__.drop(bind=bind, checkfirst=True)
    inspector = inspect(bind)
    if "employees" in set(inspector.get_table_names()):
        columns = {column["name"] for column in inspector.get_columns("employees")}
        if "facility_id" in columns:
            op.drop_constraint("fk_employees_facility_id", "employees", type_="foreignkey")
            op.drop_index("ix_employees_facility_id", table_name="employees")
            op.drop_column("employees", "facility_id")
        if "custom_data" in columns:
            op.drop_column("employees", "custom_data")
