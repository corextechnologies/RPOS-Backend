import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.enums import PurchaseOrderStatus


class PurchaseOrderLineItemCreate(BaseModel):
    product_id: uuid.UUID
    ordered_quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class PurchaseOrderCreate(BaseModel):
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    supplier_id: uuid.UUID
    order_number: Optional[str] = Field(default=None, max_length=64)
    expected_delivery_at: Optional[datetime] = None
    notes: Optional[str] = None
    line_items: list[PurchaseOrderLineItemCreate] = Field(min_length=1)

    @field_validator("line_items")
    @classmethod
    def validate_unique_products(
        cls, line_items: list[PurchaseOrderLineItemCreate]
    ) -> list[PurchaseOrderLineItemCreate]:
        product_ids = [item.product_id for item in line_items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("line_items must not contain duplicate product_id values")
        return line_items


class PurchaseOrderLineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    ordered_quantity: Decimal
    received_quantity: Decimal
    unit_price: Decimal
    created_at: datetime
    updated_at: datetime


class PurchaseOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    branch_id: uuid.UUID
    supplier_id: uuid.UUID
    order_number: str
    status: PurchaseOrderStatus
    order_date: datetime
    expected_delivery_at: Optional[datetime]
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    line_items: list[PurchaseOrderLineItemRead]
