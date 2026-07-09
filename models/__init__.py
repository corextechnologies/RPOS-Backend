"""
SQLAlchemy models for RPOS inventory & procurement (Sections 24.1–24.2).

Ingredient modeling: ingredients are a flagged subset of Product (`is_ingredient=True`),
not a separate table.
"""

from models.db_types import (
    purchase_order_status_enum,
    stock_movement_direction_enum,
    stock_reference_type_enum,
    stock_transaction_type_enum,
)
from models.domain import (
    Branch,
    GoodsReceipt,
    Organization,
    Product,
    PurchaseOrder,
    PurchaseOrderLineItem,
    StockBatch,
    StockTransaction,
    StockTransactionLine,
    Supplier,
)
from models.enums import (
    PurchaseOrderStatus,
    StockMovementDirection,
    StockReferenceType,
    StockTransactionType,
)

__all__ = [
    "PurchaseOrderStatus",
    "StockMovementDirection",
    "StockReferenceType",
    "StockTransactionType",
    "purchase_order_status_enum",
    "stock_movement_direction_enum",
    "stock_reference_type_enum",
    "stock_transaction_type_enum",
    "Organization",
    "Branch",
    "Product",
    "Supplier",
    "PurchaseOrder",
    "PurchaseOrderLineItem",
    "GoodsReceipt",
    "StockBatch",
    "StockTransaction",
    "StockTransactionLine",
]
