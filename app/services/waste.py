"""Waste & expiry write-off history — one query for every portal.

Every WASTE/EXPIRY movement is already written to the stock ledger by
InventoryService, so listing history is a read over StockMovement. Warehouse,
kitchen and branch each scope to their own location; Admin sees them all and
can filter by location. Keeping the query and the WasteEvent shape here means
the four endpoints can never drift apart.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import StockMovement, StockMovementType
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.user import User
from app.schemas.warehouse import WasteEventOut, WasteProductSnapshot


class WasteService:
    @staticmethod
    def list_events(
        db: Session,
        *,
        restaurant_id: int,
        location_type: LocationType | None = None,
        location_id: int | None = None,
        movement_type: StockMovementType | None = None,
    ) -> list[dict]:
        """Waste/expiry write-offs, newest first, as serialized WasteEvent dicts.

        Scope with location_type/location_id for a single portal; leave them off
        for Admin's cross-location view. movement_type narrows to WASTE or EXPIRY.
        """
        stmt = (
            select(StockMovement, Product, User)
            .join(Product, Product.id == StockMovement.product_id)
            .outerjoin(User, User.id == StockMovement.actor_id)
            .where(
                StockMovement.restaurant_id == restaurant_id,
                StockMovement.movement_type.in_(
                    [StockMovementType.WASTE, StockMovementType.EXPIRY]
                ),
            )
        )
        if movement_type is not None:
            stmt = stmt.where(StockMovement.movement_type == movement_type)
        if location_type is not None:
            stmt = stmt.where(StockMovement.location_type == location_type)
        if location_id is not None:
            stmt = stmt.where(StockMovement.location_id == location_id)
        # id tiebreak so two write-offs in the same created_at tick still order
        # deterministically newest-first (insertion order).
        stmt = stmt.order_by(
            StockMovement.created_at.desc(), StockMovement.id.desc()
        )

        rows = db.execute(stmt).all()
        return [
            WasteEventOut(
                id=mv.id,
                product_id=mv.product_id,
                product=WasteProductSnapshot(id=prod.id, name=prod.name, sku=prod.sku),
                quantity=abs(mv.quantity_delta),
                movement_type=mv.movement_type.value,
                waste_reason=mv.waste_reason.value if mv.waste_reason else None,
                batch_code=mv.batch_code,
                notes=mv.notes,
                location_type=mv.location_type.value,
                location_id=mv.location_id,
                created_at=mv.created_at,
                created_by=actor.full_name if actor else None,
            ).model_dump(mode="json")
            for mv, prod, actor in rows
        ]
