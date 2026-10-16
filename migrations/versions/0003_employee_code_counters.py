"""Reserve employee codes with a locked counter.

Revision ID: 0003_employee_code_counters
Revises: 0002_employee_fields
Create Date: 2026-10-14

Existing employee codes are left unchanged. The counter starts at the highest
EMP- number already stored, or zero when there are none.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_employee_code_counters"
down_revision = "0002_employee_fields"
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    if "employee_code_counters" not in set(inspector.get_table_names(schema="hrms")):
        op.create_table(
            "employee_code_counters",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("last_value", sa.Integer(), nullable=False),
            schema="hrms",
        )
    op.execute(
        """
        INSERT INTO hrms.employee_code_counters (id, last_value)
        SELECT 1, COALESCE(MAX((regexp_match(employee_code, '^EMP-([0-9]+)$'))[1]::integer), 0)
        FROM hrms.employees
        """
    )


def downgrade():
    op.drop_table("employee_code_counters", schema="hrms")
