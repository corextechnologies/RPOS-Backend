"""POS-1: menu versions, modifiers, combos, availability, orders (supersedes branch_orders)

The branch_orders -> orders supersession. Pre-launch (no production rows), so the
copy/verify backfill the plan sketched for a live system is not needed: the old
tables are dropped and the new ones start empty. If this is ever run against a
populated DB it will simply drop real sales, so the guard below refuses.

/v1/branch/orders keeps working — it is now a thin wrapper over `orders` and its
regression tests pass unmodified.

Revision ID: 0015_pos_1
Revises: 0014_pos_0
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0015_pos_1"
down_revision: Union[str, None] = "0014_pos_0"
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

    # Refuse to silently destroy real sales. Pre-launch this is 0 and the
    # migration proceeds; anywhere else, stop and write a backfill first.
    # Skipped in offline (--sql) mode, which has no connection to ask.
    if not context.is_offline_mode():
        existing = bind.execute(sa.text("SELECT count(*) FROM branch_orders")).scalar()
        if existing:
            raise RuntimeError(
                f"branch_orders holds {existing} row(s). This migration was "
                "written for a pre-launch database and would drop real sales. "
                "Write the branch_orders -> orders backfill before running it."
            )

    # --- menu ---
    # create_type=False throughout: each type is created explicitly here, so
    # create_table below must not try to create it again.
    menu_status = postgresql.ENUM(
        "DRAFT", "PUBLISHED", "ARCHIVED", name="menu_version_status",
        create_type=False,
    )
    menu_status.create(bind, checkfirst=True)
    op.create_table(
        "menu_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "restaurant_id", sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", menu_status, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="PKR"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_menu_versions_restaurant_id", "menu_versions", ["restaurant_id"])
    op.create_index("ix_menu_versions_published_by_id", "menu_versions", ["published_by_id"])
    op.create_unique_constraint(
        "uq_menu_version_restaurant_no", "menu_versions", ["restaurant_id", "version_no"]
    )

    op.create_table(
        "menu_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "menu_version_id", sa.Integer(),
            sa.ForeignKey("menu_versions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "product_id", sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("is_combo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_menu_items_menu_version_id", "menu_items", ["menu_version_id"])
    op.create_index("ix_menu_items_product_id", "menu_items", ["product_id"])
    op.create_index("ix_menu_items_category", "menu_items", ["category"])

    op.create_table(
        "combo_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "combo_item_id", sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "component_item_id", sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_combo_components_combo_item_id", "combo_components", ["combo_item_id"])
    op.create_index(
        "ix_combo_components_component_item_id", "combo_components", ["component_item_id"]
    )

    op.create_table(
        "modifier_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "menu_version_id", sa.Integer(),
            sa.ForeignKey("menu_versions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("min_select", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_select", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_modifier_groups_menu_version_id", "modifier_groups", ["menu_version_id"])

    op.create_table(
        "modifier_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "group_id", sa.Integer(),
            sa.ForeignKey("modifier_groups.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("price_delta_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "product_id", sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_modifier_options_group_id", "modifier_options", ["group_id"])
    op.create_index("ix_modifier_options_product_id", "modifier_options", ["product_id"])

    op.create_table(
        "menu_item_modifier_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "menu_item_id", sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "group_id", sa.Integer(),
            sa.ForeignKey("modifier_groups.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "ix_menu_item_modifier_groups_menu_item_id", "menu_item_modifier_groups", ["menu_item_id"]
    )
    op.create_index(
        "ix_menu_item_modifier_groups_group_id", "menu_item_modifier_groups", ["group_id"]
    )
    op.create_unique_constraint(
        "uq_menu_item_modifier_group", "menu_item_modifier_groups", ["menu_item_id", "group_id"]
    )

    op.create_table(
        "item_availability",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "restaurant_id", sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "branch_id", sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "menu_item_id", sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column(
            "marked_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("auto_clear_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_item_availability_restaurant_id", "item_availability", ["restaurant_id"])
    op.create_index("ix_item_availability_branch_id", "item_availability", ["branch_id"])
    op.create_index("ix_item_availability_menu_item_id", "item_availability", ["menu_item_id"])
    op.create_unique_constraint(
        "uq_availability_branch_item", "item_availability", ["branch_id", "menu_item_id"]
    )

    # --- orders (supersedes branch_orders) ---
    order_channel = postgresql.ENUM(
        "COUNTER", "CURBSIDE", "DELIVERY", "PICKUP", name="order_channel",
        create_type=False,
    )
    order_channel.create(bind, checkfirst=True)
    order_type = postgresql.ENUM(
        "DINE_IN", "TAKEAWAY", "CURBSIDE", "DELIVERY", "PICKUP", name="order_type",
        create_type=False,
    )
    order_type.create(bind, checkfirst=True)
    order_status = postgresql.ENUM(
        "DRAFT", "PARKED", "SENT", "PREPARING", "READY", "SERVED", "VOID",
        name="order_status", create_type=False,
    )
    order_status.create(bind, checkfirst=True)
    void_state = postgresql.ENUM(
        "ACTIVE", "VOIDED", name="void_state", create_type=False
    )
    void_state.create(bind, checkfirst=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "restaurant_id", sa.Integer(),
            sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "branch_id", sa.Integer(),
            sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("order_no", sa.String(length=64), nullable=True),
        sa.Column("local_id", sa.String(length=64), nullable=False),
        sa.Column("channel", order_channel, nullable=False),
        sa.Column("order_type", order_type, nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column(
            "customer_id", sa.Integer(),
            sa.ForeignKey("customers.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "opened_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "device_id", sa.Integer(),
            sa.ForeignKey("pos_devices.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("shift_id", sa.Integer(), nullable=True),
        sa.Column(
            "menu_version_id", sa.Integer(),
            sa.ForeignKey("menu_versions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "tax_profile_id", sa.Integer(),
            sa.ForeignKey("tax_profiles.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("country_pack", sa.String(length=8), nullable=True),
        sa.Column("pack_version", sa.String(length=32), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="PKR"),
        sa.Column("subtotal_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("discount_total_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_total_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("service_charge_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rounding_adj_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("grand_total_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("vehicle_plate", sa.String(length=32), nullable=True),
        sa.Column("vehicle_colour", sa.String(length=32), nullable=True),
        sa.Column("bay_no", sa.String(length=16), nullable=True),
        sa.Column("table_no", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "flagged_for_review", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    for col in ("restaurant_id", "branch_id", "order_no", "local_id", "customer_id",
                "opened_by_id", "device_id", "shift_id", "menu_version_id",
                "tax_profile_id", "occurred_at", "vehicle_plate"):
        op.create_index(f"ix_orders_{col}", "orders", [col])
    op.create_unique_constraint(
        "uq_order_branch_local_id", "orders", ["branch_id", "local_id"]
    )

    op.create_table(
        "order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "order_id", sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column(
            "menu_item_id", sa.Integer(),
            sa.ForeignKey("menu_items.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "product_id", sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("line_discount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("net_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tax_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("seat_no", sa.Integer(), nullable=True),
        sa.Column(
            "parent_line_id", sa.Integer(),
            sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("void_state", void_state, nullable=False, server_default="ACTIVE"),
        sa.Column("void_reason_code", sa.String(length=50), nullable=True),
        sa.Column(
            "voided_by_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    for col in ("order_id", "menu_item_id", "product_id", "parent_line_id", "voided_by_id"):
        op.create_index(f"ix_order_lines_{col}", "order_lines", [col])

    op.create_table(
        "order_line_modifiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column(
            "order_line_id", sa.Integer(),
            sa.ForeignKey("order_lines.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "group_id", sa.Integer(),
            sa.ForeignKey("modifier_groups.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "option_id", sa.Integer(),
            sa.ForeignKey("modifier_options.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price_delta_minor", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_order_line_modifiers_order_line_id", "order_line_modifiers", ["order_line_id"]
    )

    # --- sales_records: repoint the FK at the new authoritative table ---
    op.drop_constraint("uq_sales_records_branch_order_id", "sales_records", type_="unique")
    op.drop_index("ix_sales_records_branch_order_id", table_name="sales_records")
    op.drop_column("sales_records", "branch_order_id")
    op.add_column(
        "sales_records",
        sa.Column(
            "order_id", sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_unique_constraint("uq_sales_records_order_id", "sales_records", ["order_id"])
    op.create_index("ix_sales_records_order_id", "sales_records", ["order_id"])

    # --- drop the superseded tables ---
    op.drop_table("branch_order_lines")
    op.drop_table("branch_orders")


def downgrade() -> None:
    raise NotImplementedError(
        "0015 drops branch_orders in favour of `orders`. Rolling back would mean "
        "reconstructing the old tables from POS orders that have no equivalent "
        "shape (channels, modifiers, combos). Restore from a backup instead."
    )
