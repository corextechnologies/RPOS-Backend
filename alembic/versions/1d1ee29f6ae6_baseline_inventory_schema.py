"""baseline inventory schema

Revision ID: 1d1ee29f6ae6
Revises:
Create Date: 2025-07-09 12:30:00
"""

from pathlib import Path

from alembic import op

revision = "1d1ee29f6ae6"
down_revision = None
branch_labels = None
depends_on = None

_BASELINE_SQL = (Path(__file__).parent / "baseline_inventory_schema.sql").read_text(
    encoding="utf-8"
)


def upgrade() -> None:
    op.execute(_BASELINE_SQL)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS stock_transaction_lines CASCADE;
        DROP TABLE IF EXISTS stock_transactions CASCADE;
        DROP TABLE IF EXISTS stock_batches CASCADE;
        DROP TABLE IF EXISTS goods_receipts CASCADE;
        DROP TABLE IF EXISTS purchase_order_line_items CASCADE;
        DROP TABLE IF EXISTS purchase_orders CASCADE;
        DROP TABLE IF EXISTS suppliers CASCADE;
        DROP TABLE IF EXISTS products CASCADE;
        DROP TABLE IF EXISTS branches CASCADE;
        DROP TABLE IF EXISTS organizations CASCADE;
        DROP TYPE IF EXISTS "StockReferenceType";
        DROP TYPE IF EXISTS "StockMovementDirection";
        DROP TYPE IF EXISTS "StockTransactionType";
        DROP TYPE IF EXISTS "PurchaseOrderStatus";
        """
    )
