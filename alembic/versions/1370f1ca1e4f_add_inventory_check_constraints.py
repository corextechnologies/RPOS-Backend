"""add inventory check constraints

Revision ID: 1370f1ca1e4f
Revises: 1d1ee29f6ae6
Create Date: 2025-07-09 12:50:00
"""

from alembic import op

revision = "1370f1ca1e4f"
down_revision = "1d1ee29f6ae6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_po_line_ordered_qty_positive",
        "purchase_order_line_items",
        "ordered_quantity > 0",
    )
    op.create_check_constraint(
        "ck_po_line_received_qty_nonneg",
        "purchase_order_line_items",
        "received_quantity >= 0",
    )
    op.create_check_constraint(
        "ck_po_line_received_lte_ordered",
        "purchase_order_line_items",
        "received_quantity <= ordered_quantity",
    )
    op.create_check_constraint(
        "ck_po_line_unit_price_nonneg",
        "purchase_order_line_items",
        "unit_price >= 0",
    )
    op.create_check_constraint(
        "ck_stock_batch_received_qty_positive",
        "stock_batches",
        "received_quantity > 0",
    )
    op.create_check_constraint(
        "ck_stock_batch_on_hand_nonneg",
        "stock_batches",
        "quantity_on_hand >= 0",
    )
    op.create_check_constraint(
        "ck_stock_batch_on_hand_lte_received",
        "stock_batches",
        "quantity_on_hand <= received_quantity",
    )
    op.create_check_constraint(
        "ck_stock_tx_line_qty_positive",
        "stock_transaction_lines",
        "quantity > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_stock_tx_line_qty_positive", "stock_transaction_lines", type_="check")
    op.drop_constraint("ck_stock_batch_on_hand_lte_received", "stock_batches", type_="check")
    op.drop_constraint("ck_stock_batch_on_hand_nonneg", "stock_batches", type_="check")
    op.drop_constraint("ck_stock_batch_received_qty_positive", "stock_batches", type_="check")
    op.drop_constraint("ck_po_line_unit_price_nonneg", "purchase_order_line_items", type_="check")
    op.drop_constraint("ck_po_line_received_lte_ordered", "purchase_order_line_items", type_="check")
    op.drop_constraint("ck_po_line_received_qty_nonneg", "purchase_order_line_items", type_="check")
    op.drop_constraint("ck_po_line_ordered_qty_positive", "purchase_order_line_items", type_="check")
