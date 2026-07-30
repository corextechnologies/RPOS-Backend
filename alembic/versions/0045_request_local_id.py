"""Add local_id to requests for offline-replay idempotency

Purely additive, nullable. Unique (restaurant_id, local_id) makes a requisition
created offline replay to the same row instead of double-creating (mirrors
Order.local_id). NULLs are distinct in Postgres, so existing rows are unaffected.

Revision ID: 0045_request_local_id
Revises: 0044_payment_account_ref
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0045_request_local_id"
down_revision: Union[str, None] = "0044_payment_account_ref"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requests", sa.Column("local_id", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "uq_request_restaurant_local_id", "requests", ["restaurant_id", "local_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_request_restaurant_local_id", "requests", type_="unique")
    op.drop_column("requests", "local_id")
