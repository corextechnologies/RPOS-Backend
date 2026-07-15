"""Phase 5 Branch: BRANCH_STAFF role, branch_position, users.position

Revision ID: 0009_phase_5_branch_staff
Revises: 0008_phase_4_kitchen
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0009_phase_5_branch_staff"
down_revision: Union[str, None] = "0008_phase_4_kitchen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False (same rule as 0006/0008): create the type once, explicitly,
# so op.add_column() does not re-emit CREATE TYPE.
branch_position = postgresql.ENUM(
    "SALESPERSON",
    "CASHIER",
    "ORDER_TAKER",
    name="branch_position",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # PG 12+ permits ALTER TYPE ... ADD VALUE in a transaction as long as the new
    # value is not *used* in the same transaction. Nothing below inserts a
    # BRANCH_STAFF row, so this is safe. Do not add one here.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'BRANCH_STAFF'")

    branch_position.create(bind, checkfirst=True)
    op.add_column(
        "users",
        sa.Column("position", branch_position, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "position")
    branch_position.drop(op.get_bind(), checkfirst=True)

    # Postgres cannot remove a value from an enum type, so BRANCH_STAFF stays on
    # user_role after a downgrade. It is inert unless a row references it.
