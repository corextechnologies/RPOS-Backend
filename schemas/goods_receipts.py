import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GoodsReceiptBatchCreate(BaseModel):
    product_id: uuid.UUID
    batch_number: str = Field(min_length=1, max_length=64)
    expiry_date: Optional[date] = None
    quantity_received: Decimal = Field(gt=0)
    unit_cost: Optional[Decimal] = Field(default=None, ge=0)


class GoodsReceiptCreate(BaseModel):
    organization_id: uuid.UUID
    purchase_order_id: uuid.UUID
    receipt_number: Optional[str] = Field(default=None, max_length=64)
    notes: Optional[str] = None
    batches: list[GoodsReceiptBatchCreate] = Field(min_length=1)

    @field_validator("batches")
    @classmethod
    def validate_unique_batch_numbers(
        cls, batches: list[GoodsReceiptBatchCreate]
    ) -> list[GoodsReceiptBatchCreate]:
        keys = [(b.product_id, b.batch_number) for b in batches]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "batches must not contain duplicate product_id and batch_number pairs"
            )
        return batches


class StockBatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    batch_number: str
    expiry_date: Optional[date]
    received_quantity: Decimal
    quantity_on_hand: Decimal
    unit_cost: Optional[Decimal]
    created_at: datetime
    updated_at: datetime


class StockTransactionLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    stock_batch_id: uuid.UUID
    direction: str
    quantity: Decimal
    unit_cost: Optional[Decimal]
    created_at: datetime


class StockTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference_type: str
    reference_id: uuid.UUID
    goods_receipt_id: Optional[uuid.UUID]
    transaction_type: str
    reference_number: str
    occurred_at: datetime
    notes: Optional[str]
    created_at: datetime
    lines: list[StockTransactionLineRead]


class GoodsReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    purchase_order_id: uuid.UUID
    receipt_number: str
    received_at: datetime
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    stock_batches: list[StockBatchRead]
    stock_transaction: StockTransactionRead
