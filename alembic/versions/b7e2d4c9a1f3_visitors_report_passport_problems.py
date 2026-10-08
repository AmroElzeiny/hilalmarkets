"""A visitor without an account may report a problem on a Passport.

Until now only a member could, and the report pointed at their account. A visitor gives
their own email address instead, so a reviewer can write back. Exactly one of the two is
always there: the check below refuses a report with neither.

Revision ID: b7e2d4c9a1f3
Revises: a1d7c3e95f20
Create Date: 2026-10-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7e2d4c9a1f3"
down_revision = "a1d7c3e95f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sharia_passport_problem_reports") as batch:
        batch.add_column(sa.Column("reporter_email", sa.String(length=320), nullable=True))
        batch.alter_column("reporter_user_id", existing_type=sa.Uuid(), nullable=True)
        batch.create_check_constraint(
            op.f("ck_sharia_passport_problem_reports_reporter_present"),
            "reporter_user_id IS NOT NULL OR reporter_email IS NOT NULL",
        )


def downgrade() -> None:
    # A visitor's report has no account to point at, so it cannot survive going back.
    op.execute("DELETE FROM sharia_passport_problem_reports WHERE reporter_user_id IS NULL")
    with op.batch_alter_table("sharia_passport_problem_reports") as batch:
        batch.drop_constraint(
            op.f("ck_sharia_passport_problem_reports_reporter_present"), type_="check"
        )
        batch.alter_column("reporter_user_id", existing_type=sa.Uuid(), nullable=False)
        batch.drop_column("reporter_email")
