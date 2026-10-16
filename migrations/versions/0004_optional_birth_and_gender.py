"""Allow a roster import to omit date of birth and gender.

Revision ID: 0004_optional_birth_and_gender
Revises: 0003_employee_code_counters
Create Date: 2026-10-15

The personal form still requires both values when someone saves that form.
This only lets an imported name exist before those details are known.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_optional_birth_and_gender"
down_revision = "0003_employee_code_counters"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("employees", "date_of_birth", existing_type=sa.Date(), nullable=True, schema="hrms")
    op.alter_column("employees", "gender", existing_type=sa.String(length=16), nullable=True, schema="hrms")


def downgrade():
    op.alter_column("employees", "date_of_birth", existing_type=sa.Date(), nullable=False, schema="hrms")
    op.alter_column("employees", "gender", existing_type=sa.String(length=16), nullable=False, schema="hrms")
