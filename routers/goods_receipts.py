import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from database import get_db
from schemas.goods_receipts import (
    GoodsReceiptCreate,
    GoodsReceiptRead,
    TemperatureReadingCreate,
    TemperatureReadingRead,
)
from services.goods_receipts import create_goods_receipt
from services.temperature_readings import record_temperature_reading

router = APIRouter(prefix="/goods-receipts", tags=["goods-receipts"])


@router.post(
    "",
    response_model=GoodsReceiptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Receive goods against a purchase order",
)
def post_goods_receipt(
    payload: GoodsReceiptCreate,
    db: Session = Depends(get_db),
) -> GoodsReceiptRead:
    return create_goods_receipt(db, payload)


@router.post(
    "/{goods_receipt_id}/temperature-reading",
    response_model=TemperatureReadingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a cold-chain temperature reading for a goods receipt",
)
def post_goods_receipt_temperature_reading(
    goods_receipt_id: uuid.UUID,
    payload: TemperatureReadingCreate,
    db: Session = Depends(get_db),
) -> TemperatureReadingRead:
    return record_temperature_reading(db, goods_receipt_id, payload)
