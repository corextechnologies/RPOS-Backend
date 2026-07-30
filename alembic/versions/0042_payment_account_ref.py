"""Link a payment to the account it was paid into (ONLINE tender)

Purely additive, nullable. Also documents that the pre-existing (previously
unused) payments.idempotency_key column + uq_payment_idempotency now carry the
device-minted client_payment_id business-dedup anchor — no schema change needed
for that, it is reused.

Revision ID: 0042_payment_account_ref
Revises: 0041_payment_accounts
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0042_payment_account_ref"
down_revision: Union[str, None] = "0041_payment_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column(
            "payment_account_id",
            sa.Integer(),
            sa.ForeignKey("payment_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_payments_payment_account_id", "payments", ["payment_account_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payments_payment_account_id", table_name="payments")
    op.drop_column("payments", "payment_account_id")
