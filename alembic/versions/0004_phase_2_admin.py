"""Phase 2: Admin portal — cost_price, manager location FKs on users

Revision ID: 0004_phase_2
Revises: 0003_phase_6a
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_phase_2"
down_revision: Union[str, None] = "0003_phase_6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("kitchen_id", sa.Integer(), sa.ForeignKey("kitchens.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_users_branch_id", "users", ["branch_id"])
    op.create_index("ix_users_kitchen_id", "users", ["kitchen_id"])
    op.create_index("ix_users_warehouse_id", "users", ["warehouse_id"])


def downgrade() -> None:
    op.drop_index("ix_users_warehouse_id", table_name="users")
    op.drop_index("ix_users_kitchen_id", table_name="users")
    op.drop_index("ix_users_branch_id", table_name="users")
    op.drop_column("users", "warehouse_id")
    op.drop_column("users", "kitchen_id")
    op.drop_column("users", "branch_id")
    op.drop_column("products", "cost_price")
