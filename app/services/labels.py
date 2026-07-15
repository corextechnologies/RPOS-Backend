"""Expiry sticker / label data.

Returns structured label rows only — product, batch, expiry. Rendering and
printing are a frontend/print-service concern and must stay out of here.

Shared on purpose: Phase 4 wires it to the Kitchen, Phase 9 formalizes the same
endpoint for the Warehouse. Do not fork a per-portal copy.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import InventoryItem
from app.models.product import Product
from app.models.request_enums import LocationType


class LabelService:
    @staticmethod
    def labels_for_location(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType,
        location_id: int,
        product_id: int | None = None,
        batch_code: str | None = None,
    ) -> list[dict]:
        stmt = (
            select(InventoryItem, Product)
            .join(Product, Product.id == InventoryItem.product_id)
            .where(
                InventoryItem.restaurant_id == restaurant_id,
                InventoryItem.location_type == location_type,
                InventoryItem.location_id == location_id,
                InventoryItem.quantity > 0,
            )
        )
        if product_id is not None:
            stmt = stmt.where(InventoryItem.product_id == product_id)
        if batch_code is not None:
            stmt = stmt.where(InventoryItem.batch_code == batch_code.strip())

        rows = db.execute(
            stmt.order_by(InventoryItem.expiry_date, InventoryItem.id)
        ).all()

        # cost_price is deliberately absent — labels are printed on the floor.
        return [
            {
                "product_id": product.id,
                "product_name": product.name,
                "sku": product.sku,
                "batch_code": item.batch_code,
                "expiry_date": item.expiry_date,
                "quantity": item.quantity,
                "location_type": location_type.value,
                "location_id": location_id,
            }
            for item, product in rows
        ]
