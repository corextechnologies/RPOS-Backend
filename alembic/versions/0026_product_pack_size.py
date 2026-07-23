"""Product.pack_size: decimal purchase-pack size for weight/volume raw materials.

"1 bag = 5 kg" — the amount of stock_unit in one purchase pack. Weight/volume
stays the single source of truth in inventory; packs are a display/entry layer
(weight = packs x pack_size). Distinct from the integer units_per_pack (count
cartons). Nullable; only meaningful for RAW_MATERIAL weight/volume products,
enforced at the API.

Revision ID: 0026_product_pack_size
Revises: 0025_fractional_stock
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026_product_pack_size"
down_revision: Union[str, None] = "0025_fractional_stock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("pack_size", sa.Numeric(12, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "pack_size")
