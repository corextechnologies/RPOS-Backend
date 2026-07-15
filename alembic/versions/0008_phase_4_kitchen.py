"""Phase 4 Cloud Kitchen: SUB_CHEF role, waste reasons, stock counts

Revision ID: 0008_phase_4_kitchen
Revises: 0007_phase_2_extras
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0008_phase_4_kitchen"
down_revision: Union[str, None] = "0007_phase_2_extras"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same create_type=False rule as 0006: we create the type once, explicitly, so
# op.add_column() does not re-emit CREATE TYPE.
waste_reason = postgresql.ENUM(
    "SPOILAGE",
    "EXPIRED",
    "DAMAGED",
    "OVERPRODUCTION",
    "PREP_ERROR",
    "OTHER",
    name="waste_reason",
    create_type=False,
)

# Reuse location_type created in Phase 6A — do not recreate.
location_type = postgresql.ENUM(
    "BRANCH",
    "KITCHEN",
    "WAREHOUSE",
    name="location_type",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction as long as the
    # new value is not *used* in the same transaction. Nothing below inserts a
    # SUB_CHEF row, so this is safe. Do not add one here.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'SUB_CHEF'")

    waste_reason.create(bind, checkfirst=True)
    op.add_column(
        "stock_movements",
        sa.Column("waste_reason", waste_reason, nullable=True),
    )

    op.create_table(
        "stock_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "restaurant_id",
            sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("location_type", location_type, nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column(
            "counted_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_stock_counts_restaurant_id", "stock_counts", ["restaurant_id"])
    op.create_index("ix_stock_counts_location_id", "stock_counts", ["location_id"])
    op.create_index("ix_stock_counts_counted_by_id", "stock_counts", ["counted_by_id"])

    op.create_table(
        "stock_count_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "stock_count_id",
            sa.Integer(),
            sa.ForeignKey("stock_counts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_code",
            sa.String(length=100),
            nullable=False,
            server_default="",
        ),
        sa.Column("counted_quantity", sa.Integer(), nullable=False),
        sa.Column("system_quantity", sa.Integer(), nullable=False),
        sa.Column("variance", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_stock_count_lines_stock_count_id", "stock_count_lines", ["stock_count_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_stock_count_lines_stock_count_id", table_name="stock_count_lines"
    )
    op.drop_table("stock_count_lines")

    op.drop_index("ix_stock_counts_counted_by_id", table_name="stock_counts")
    op.drop_index("ix_stock_counts_location_id", table_name="stock_counts")
    op.drop_index("ix_stock_counts_restaurant_id", table_name="stock_counts")
    op.drop_table("stock_counts")

    op.drop_column("stock_movements", "waste_reason")
    waste_reason.drop(op.get_bind(), checkfirst=True)

    # Postgres cannot remove a value from an enum type, so SUB_CHEF stays on
    # user_role after a downgrade. It is inert unless a row references it.
