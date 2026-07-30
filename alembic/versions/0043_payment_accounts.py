"""Admin-configured payment accounts (ONLINE pay-into-account tender)

Purely additive. A bank/wallet destination for ONLINE payments; cached on the
device via /pos/config so a cashier can show it (or a QR) offline.

Revision ID: 0043_payment_accounts
Revises: 0042_flagged_reason
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0043_payment_accounts"
down_revision: Union[str, None] = "0042_flagged_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    kind = postgresql.ENUM(
        "BANK", "WALLET", name="payment_account_kind", create_type=False
    )
    kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "payment_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "branch_id",
            sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("account_ref", sa.String(length=255), nullable=False),
        sa.Column("bank_or_wallet", sa.String(length=255), nullable=True),
        sa.Column("qr_payload", sa.String(length=1024), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_payment_accounts_restaurant_id", "payment_accounts", ["restaurant_id"])
    op.create_index("ix_payment_accounts_branch_id", "payment_accounts", ["branch_id"])


def downgrade() -> None:
    op.drop_table("payment_accounts")
    sa.Enum(name="payment_account_kind").drop(op.get_bind(), checkfirst=True)
