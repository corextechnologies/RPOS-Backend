"""Phase 8 billing: invoices table

Revision ID: 0003_billing
Revises: 0002_super_admin
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_billing"
down_revision: Union[str, None] = "0002_super_admin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("restaurant_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("paid", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["restaurant_id"], ["restaurants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("restaurant_id", "issued_on", name="uq_invoice_restaurant_issued_on"),
    )
    op.create_index("ix_invoices_restaurant_id", "invoices", ["restaurant_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_restaurant_id", table_name="invoices")
    op.drop_table("invoices")
