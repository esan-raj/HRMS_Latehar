"""Reserve facility codes with a locked counter.

Revision ID: 0005_facility_code_counters
Revises: 0004_optional_birth_and_gender
Create Date: 2026-10-16

Existing facility codes are left unchanged. The counter starts at the highest
FAC- number already stored, or zero when there are none.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_facility_code_counters"
down_revision = "0004_optional_birth_and_gender"
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    if "facility_code_counters" not in set(inspector.get_table_names(schema="hrms")):
        op.create_table(
            "facility_code_counters",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("last_value", sa.Integer(), nullable=False),
            schema="hrms",
        )
    op.execute(
        """
        INSERT INTO hrms.facility_code_counters (id, last_value)
        SELECT 1, COALESCE((
            SELECT MAX((regexp_match(code, '^FAC-([0-9]+)$'))[1]::integer) FROM hrms.facilities
        ), 0)
        WHERE NOT EXISTS (SELECT 1 FROM hrms.facility_code_counters WHERE id = 1)
        """
    )
    op.execute("ALTER TABLE hrms.facility_code_counters ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("facility_code_counters", schema="hrms")
