"""PostgreSQL enum bindings shared across SQLAlchemy models."""

from sqlalchemy import Enum

from models.enums import (
    PurchaseOrderStatus,
    StockMovementDirection,
    StockReferenceType,
    StockTransactionType,
)

purchase_order_status_enum = Enum(
    PurchaseOrderStatus, name="PurchaseOrderStatus", create_type=False
)
stock_transaction_type_enum = Enum(
    StockTransactionType, name="StockTransactionType", create_type=False
)
stock_movement_direction_enum = Enum(
    StockMovementDirection, name="StockMovementDirection", create_type=False
)
stock_reference_type_enum = Enum(
    StockReferenceType, name="StockReferenceType", create_type=False
)
