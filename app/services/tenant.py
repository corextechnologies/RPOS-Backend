"""Small tenant-topology helpers shared across services."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.restaurant import Restaurant


def has_central_kitchen(db: Session, restaurant_id: int) -> bool:
    """Whether this tenant runs a central/cloud kitchen.

    False = a kitchen-off tenant: its branch sub-kitchens make finished goods
    from raw materials on-site, so a made-to-order dish is never held as
    finished-good stock — it is sellable regardless of on-hand, deducts no
    finished good at sale, and consumes its recipe's raw materials when the chef
    completes the prep ticket. Defaults True for a missing row (fail safe to the
    long-standing central-kitchen behaviour).
    """
    r = db.get(Restaurant, restaurant_id)
    return bool(r.has_central_kitchen) if r is not None else True
