import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class StockBatchLedgerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stock_batch_id: uuid.UUID
    batch_number: str
    expiry_date: Optional[date]
    quantity_on_hand: Decimal = Field(ge=0)
    unit_cost: Optional[Decimal] = Field(default=None, ge=0)


class StockLedgerItemRead(BaseModel):
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    name: str
    unit_of_measure: str
    total_quantity_on_hand: Decimal = Field(ge=0)
    batches: list[StockBatchLedgerRead]


class StockLedgerRead(BaseModel):
    items: list[StockLedgerItemRead]


class LowStockItemRead(BaseModel):
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    name: str
    unit_of_measure: str
    reorder_level: Decimal = Field(gt=0)
    quantity_on_hand: Decimal = Field(ge=0)
    is_low_stock: bool


class LowStockRead(BaseModel):
    items: list[LowStockItemRead]
