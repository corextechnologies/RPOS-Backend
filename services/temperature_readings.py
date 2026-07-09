import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from models import GoodsReceipt, GoodsReceiptTemperatureReading
from models.master_data import TemperatureRange
from schemas.goods_receipts import (
    TemperatureRangeSnapshotRead,
    TemperatureReadingCreate,
    TemperatureReadingRead,
)


def _temperature_ranges_available(db: Session) -> bool:
    return inspect(db.bind).has_table("temperature_ranges")


def _is_within_range(
    temperature_celsius: Decimal,
    min_celsius: Decimal,
    max_celsius: Decimal | None,
) -> bool:
    if temperature_celsius < min_celsius:
        return False
    if max_celsius is not None and temperature_celsius > max_celsius:
        return False
    return True


def _to_read_model(
    reading: GoodsReceiptTemperatureReading,
    temperature_range: TemperatureRange | None,
) -> TemperatureReadingRead:
    return TemperatureReadingRead(
        id=reading.id,
        goods_receipt_id=reading.goods_receipt_id,
        organization_id=reading.organization_id,
        temperature_range_id=reading.temperature_range_id,
        temperature_range=(
            TemperatureRangeSnapshotRead.model_validate(temperature_range)
            if temperature_range is not None
            else None
        ),
        recorded_temperature_celsius=reading.recorded_temperature_celsius,
        is_within_range=reading.is_within_range,
        recorded_at=reading.recorded_at,
        notes=reading.notes,
        created_at=reading.created_at,
    )


def record_temperature_reading(
    db: Session,
    goods_receipt_id: uuid.UUID,
    payload: TemperatureReadingCreate,
) -> TemperatureReadingRead:
    goods_receipt = db.scalar(
        select(GoodsReceipt).where(
            GoodsReceipt.id == goods_receipt_id,
            GoodsReceipt.organization_id == payload.organization_id,
        )
    )
    if not goods_receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Goods receipt not found for this organization",
        )

    temperature_range: TemperatureRange | None = None
    is_within_range: bool | None = None

    if payload.temperature_range_id is not None:
        if _temperature_ranges_available(db):
            temperature_range = db.get(TemperatureRange, payload.temperature_range_id)
            if not temperature_range:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"Temperature range {payload.temperature_range_id} was not found"
                    ),
                )
            is_within_range = _is_within_range(
                payload.temperature_celsius,
                temperature_range.min_celsius,
                temperature_range.max_celsius,
            )

    reading = GoodsReceiptTemperatureReading(
        organization_id=payload.organization_id,
        goods_receipt_id=goods_receipt.id,
        temperature_range_id=payload.temperature_range_id,
        recorded_temperature_celsius=payload.temperature_celsius,
        is_within_range=is_within_range,
        notes=payload.notes,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)

    return _to_read_model(reading, temperature_range)
