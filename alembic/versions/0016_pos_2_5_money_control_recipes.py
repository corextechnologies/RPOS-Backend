"""POS-2/3/5: payments, refunds, discounts, shifts, drawer, recipes, stock units

Revision ID: 0016_pos_2_5
Revises: 0015_pos_1
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0016_pos_2_5"
down_revision: Union[str, None] = "0015_pos_1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True),
        server_default=sa.func.now(), nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()

    # create_type=False throughout: each type is created explicitly, so
    # create_table must not create it a second time.
    payment_method = postgresql.ENUM(
        "CASH", "CARD", "WALLET", "ONLINE", name="payment_method", create_type=False
    )
    payment_method.create(bind, checkfirst=True)
    payment_status = postgresql.ENUM(
        "PENDING", "CAPTURED", "FAILED", "REVERSED", "REFUNDED",
        name="payment_status", create_type=False,
    )
    payment_status.create(bind, checkfirst=True)
    reason_code = postgresql.ENUM(
        "CUSTOMER_CHANGED_MIND", "KITCHEN_ERROR", "WRONG_ENTRY", "SPOILAGE",
        "QUALITY_COMPLAINT", "LATE_DELIVERY", "PRICE_ADJUSTMENT", "TRAINING",
        "OTHER", name="reason_code", create_type=False,
    )
    reason_code.create(bind, checkfirst=True)
    shift_status = postgresql.ENUM(
        "OPEN", "CLOSED", name="shift_status", create_type=False
    )
    shift_status.create(bind, checkfirst=True)
    cash_movement_type = postgresql.ENUM(
        "PAID_IN", "PAID_OUT", "DROP", "PICKUP", "NO_SALE",
        name="cash_movement_type", create_type=False,
    )
    cash_movement_type.create(bind, checkfirst=True)
    discount_scope = postgresql.ENUM(
        "ORDER", "LINE", name="discount_scope", create_type=False
    )
    discount_scope.create(bind, checkfirst=True)
    discount_type = postgresql.ENUM(
        "PCT", "AMT", name="discount_type", create_type=False
    )
    discount_type.create(bind, checkfirst=True)
    stock_unit = postgresql.ENUM(
        "EACH", "GRAM", "ML", name="stock_unit", create_type=False
    )
    stock_unit.create(bind, checkfirst=True)

    # --- shifts must exist before payments references them ---
    op.create_table(
        "shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.Integer(),
                  sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("pos_devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", shift_status, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opening_float_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("declared_cash_minor", sa.BigInteger(), nullable=True),
        sa.Column("expected_cash_minor", sa.BigInteger(), nullable=True),
        sa.Column("variance_minor", sa.BigInteger(), nullable=True),
        sa.Column("closed_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    for col in ("restaurant_id", "branch_id", "device_id", "user_id", "opened_at",
                "closed_by_id", "approved_by_id"):
        op.create_index(f"ix_shifts_{col}", "shifts", [col])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.Integer(),
                  sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", payment_method, nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("tendered_minor", sa.BigInteger(), nullable=True),
        sa.Column("change_minor", sa.BigInteger(), nullable=True),
        sa.Column("status", payment_status, nullable=False),
        sa.Column("processor_ref", sa.String(length=128), nullable=True),
        sa.Column("auth_code", sa.String(length=64), nullable=True),
        sa.Column("masked_pan", sa.String(length=24), nullable=True),
        sa.Column("terminal_id", sa.Integer(),
                  sa.ForeignKey("pos_devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("shift_id", sa.Integer(),
                  sa.ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("taken_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("restaurant_id", "order_id", "terminal_id", "shift_id",
                "taken_by_id", "occurred_at"):
        op.create_index(f"ix_payments_{col}", "payments", [col])
    op.create_unique_constraint(
        "uq_payment_idempotency", "payments", ["restaurant_id", "idempotency_key"]
    )

    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("order_id", sa.Integer(),
                  sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_id", sa.Integer(),
                  sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("method", payment_method, nullable=False),
        sa.Column("reason_code", reason_code, nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("approved_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("refunded_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("shift_id", sa.Integer(),
                  sa.ForeignKey("shifts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("restaurant_id", "order_id", "payment_id", "reason_code",
                "approved_by_id", "refunded_by_id", "shift_id", "occurred_at"):
        op.create_index(f"ix_refunds_{col}", "refunds", [col])

    op.create_table(
        "discount_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("scope", discount_scope, nullable=False),
        sa.Column("type", discount_type, nullable=False),
        sa.Column("value_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_pct_bp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_discount_rules_restaurant_id", "discount_rules", ["restaurant_id"])
    op.create_unique_constraint(
        "uq_discount_rule_code", "discount_rules", ["restaurant_id", "code"]
    )

    op.create_table(
        "cash_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("shift_id", sa.Integer(),
                  sa.ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", cash_movement_type, nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reason_code", reason_code, nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("actor_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    for col in ("shift_id", "reason_code", "actor_id", "approved_by_id", "occurred_at"):
        op.create_index(f"ix_cash_movements_{col}", "cash_movements", [col])

    # --- POS-5: recipes + stock units ---
    op.add_column(
        "products",
        sa.Column("stock_unit", stock_unit, nullable=False, server_default="EACH"),
    )
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("restaurant_id", sa.Integer(),
                  sa.ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.Integer(),
                  sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("yield_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("note", sa.String(length=500), nullable=True),
    )
    op.create_index("ix_recipes_restaurant_id", "recipes", ["restaurant_id"])
    op.create_index("ix_recipes_product_id", "recipes", ["product_id"])
    op.create_unique_constraint(
        "uq_recipe_product_version", "recipes", ["product_id", "version"]
    )

    op.create_table(
        "recipe_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        _created_at(),
        sa.Column("recipe_id", sa.Integer(),
                  sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_product_id", sa.Integer(),
                  sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("wastage_bp", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_recipe_components_recipe_id", "recipe_components", ["recipe_id"])
    op.create_index(
        "ix_recipe_components_component_product_id", "recipe_components",
        ["component_product_id"],
    )


def downgrade() -> None:
    op.drop_table("recipe_components")
    op.drop_table("recipes")
    op.drop_column("products", "stock_unit")
    op.drop_table("cash_movements")
    op.drop_table("discount_rules")
    op.drop_table("refunds")
    op.drop_table("payments")
    op.drop_table("shifts")
    for name in ("stock_unit", "discount_type", "discount_scope",
                 "cash_movement_type", "shift_status", "reason_code",
                 "payment_status", "payment_method"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
