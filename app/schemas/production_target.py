"""Schemas for daily production targets."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.production_target import ProductionTargetStatus


class ProductionTargetLineIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class ProductionTargetCreate(BaseModel):
    kitchen_id: int
    target_date: date
    note: str | None = Field(default=None, max_length=1024)
    lines: list[ProductionTargetLineIn] = Field(min_length=1)


class ProductionTargetUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=1024)
    lines: list[ProductionTargetLineIn] | None = Field(default=None, min_length=1)


class ProductionTargetLineOut(BaseModel):
    id: int
    product_id: int
    product_name: str
    quantity: int


class ProductionTargetOut(BaseModel):
    id: int
    kitchen_id: int
    kitchen_name: str
    target_date: date
    status: ProductionTargetStatus
    note: str | None
    created_at: datetime
    lines: list[ProductionTargetLineOut]
