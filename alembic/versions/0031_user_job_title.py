"""Add job_title to users — free-text label for kitchen sub-staff.

The kitchen manager types their own titles ("Head Chef", "Baker"), so this is
free text rather than an enum like BranchPosition. It is a label only: kitchen
sub-staff cannot sign in, so it grants no access.

Chained after 0030_restaurant_profile rather than branching from 0029 alongside
it, to keep a single linear head.

Revision ID: 0031_user_job_title
Revises: 0030_restaurant_profile
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031_user_job_title"
down_revision: Union[str, None] = "0030_restaurant_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("job_title", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "job_title")
