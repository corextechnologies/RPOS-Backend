"""add goods receipt temperature readings

Revision ID: a8f3c2d91b04
Revises: 002_branch_extensions
Create Date: 2026-07-09 15:10:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8f3c2d91b04"
down_revision = "002_branch_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goods_receipt_temperature_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goods_receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("temperature_range_id", sa.Integer(), nullable=True),
        sa.Column("recorded_temperature_celsius", sa.Numeric(6, 2), nullable=False),
        sa.Column("is_within_range", sa.Boolean(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["goods_receipt_id"], ["goods_receipts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "gr_temp_readings_goods_receipt_id_idx",
        "goods_receipt_temperature_readings",
        ["goods_receipt_id"],
    )
    op.create_index(
        "gr_temp_readings_org_recorded_at_idx",
        "goods_receipt_temperature_readings",
        ["organization_id", "recorded_at"],
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("temperature_ranges"):
        op.create_foreign_key(
            "fk_gr_temp_reading_temperature_range",
            "goods_receipt_temperature_readings",
            "temperature_ranges",
            ["temperature_range_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("goods_receipt_temperature_readings"):
        fk_names = {
            fk["name"]
            for fk in inspector.get_foreign_keys("goods_receipt_temperature_readings")
        }
        if "fk_gr_temp_reading_temperature_range" in fk_names:
            op.drop_constraint(
                "fk_gr_temp_reading_temperature_range",
                "goods_receipt_temperature_readings",
                type_="foreignkey",
            )

    op.drop_index(
        "gr_temp_readings_org_recorded_at_idx",
        table_name="goods_receipt_temperature_readings",
    )
    op.drop_index(
        "gr_temp_readings_goods_receipt_id_idx",
        table_name="goods_receipt_temperature_readings",
    )
    op.drop_table("goods_receipt_temperature_readings")
