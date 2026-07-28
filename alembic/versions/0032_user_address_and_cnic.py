"""Add address and CNIC document columns to users.

Every portal's staff profile now carries an address and both sides of their CNIC
(national ID). The API requires these on create, but the COLUMNS stay nullable:
staff created before this change have neither, and a NOT NULL column could not be
applied to those rows. Requirement belongs at the API, not the schema.

CNIC scans are sensitive personal documents — they live in the private bucket and
are only ever served as short-lived signed links (see app/services/storage.py).

Revision ID: 0032_user_address_and_cnic
Revises: 0031_user_job_title
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0032_user_address_and_cnic"
down_revision: Union[str, None] = "0031_user_job_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column(
        "users", sa.Column("cnic_front_url", sa.String(length=1024), nullable=True)
    )
    op.add_column(
        "users", sa.Column("cnic_back_url", sa.String(length=1024), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "cnic_back_url")
    op.drop_column("users", "cnic_front_url")
    op.drop_column("users", "address")
