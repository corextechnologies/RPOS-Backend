"""POS printing routing: stations, printers, category map, item overrides

Adds the per-branch POS print topology (Phase 0). These tables are POS-printing
only and unrelated to the Kitchen commissary or ProductionRun. Purely additive —
no existing table changes.

Revision ID: 0038_printing_routing
Revises: 0037_menu_item_made_to_order
Create Date: 2026-07-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0038_printing_routing"
down_revision: Union[str, None] = "0037_menu_item_made_to_order"
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
    bind = op.get_bind()

    # create_type=False + explicit .create(checkfirst): the type is created once
    # here so create_table doesn't try to create it a second time.
    printer_role = postgresql.ENUM(
        "KITCHEN", "RECEIPT", name="printer_role", create_type=False
    )
    printer_connection = postgresql.ENUM(
        "LAN", "USB", "BT", name="printer_connection", create_type=False
    )
    printer_protocol = postgresql.ENUM(
        "ESC_POS", "STAR", name="printer_protocol", create_type=False
    )
    printer_status = postgresql.ENUM(
        "UNKNOWN", "ONLINE", "OFFLINE", "ERROR", name="printer_status",
        create_type=False,
    )
    for enum_type in (printer_role, printer_connection, printer_protocol, printer_status):
        enum_type.create(bind, checkfirst=True)

    # --- pos_stations ---
    op.create_table(
        "pos_stations",
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
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_expo", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index("ix_pos_stations_restaurant_id", "pos_stations", ["restaurant_id"])
    op.create_index("ix_pos_stations_branch_id", "pos_stations", ["branch_id"])
    op.create_unique_constraint(
        "uq_station_branch_code", "pos_stations", ["branch_id", "code"]
    )
    # At most one expo/fallback station per branch (partial unique index).
    op.create_index(
        "uq_station_branch_one_expo",
        "pos_stations",
        ["branch_id"],
        unique=True,
        postgresql_where=sa.text("is_expo"),
    )

    # --- pos_printers ---
    op.create_table(
        "pos_printers",
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
            nullable=False,
        ),
        sa.Column("role", printer_role, nullable=False),
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("pos_stations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("pos_devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("connection", printer_connection, nullable=False),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column(
            "protocol", printer_protocol, nullable=False, server_default="ESC_POS"
        ),
        sa.Column(
            "status", printer_status, nullable=False, server_default="UNKNOWN"
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pos_printers_restaurant_id", "pos_printers", ["restaurant_id"])
    op.create_index("ix_pos_printers_branch_id", "pos_printers", ["branch_id"])
    op.create_index("ix_pos_printers_station_id", "pos_printers", ["station_id"])
    op.create_index("ix_pos_printers_device_id", "pos_printers", ["device_id"])

    # --- pos_station_category_maps ---
    op.create_table(
        "pos_station_category_maps",
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
            nullable=False,
        ),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("pos_stations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pos_station_category_maps_restaurant_id",
        "pos_station_category_maps",
        ["restaurant_id"],
    )
    op.create_index(
        "ix_pos_station_category_maps_branch_id",
        "pos_station_category_maps",
        ["branch_id"],
    )
    op.create_index(
        "ix_pos_station_category_maps_station_id",
        "pos_station_category_maps",
        ["station_id"],
    )
    op.create_unique_constraint(
        "uq_station_category_branch_category",
        "pos_station_category_maps",
        ["branch_id", "category"],
    )

    # --- pos_menu_item_stations ---
    op.create_table(
        "pos_menu_item_stations",
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
            nullable=False,
        ),
        sa.Column(
            "menu_item_id",
            sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("pos_stations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pos_menu_item_stations_restaurant_id",
        "pos_menu_item_stations",
        ["restaurant_id"],
    )
    op.create_index(
        "ix_pos_menu_item_stations_branch_id",
        "pos_menu_item_stations",
        ["branch_id"],
    )
    op.create_index(
        "ix_pos_menu_item_stations_menu_item_id",
        "pos_menu_item_stations",
        ["menu_item_id"],
    )
    op.create_index(
        "ix_pos_menu_item_stations_station_id",
        "pos_menu_item_stations",
        ["station_id"],
    )
    op.create_unique_constraint(
        "uq_menu_item_station_branch_item",
        "pos_menu_item_stations",
        ["branch_id", "menu_item_id"],
    )


def downgrade() -> None:
    op.drop_table("pos_menu_item_stations")
    op.drop_table("pos_station_category_maps")
    op.drop_table("pos_printers")
    op.drop_table("pos_stations")

    for name in ("printer_status", "printer_protocol", "printer_connection", "printer_role"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
