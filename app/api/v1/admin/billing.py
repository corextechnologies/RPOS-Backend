"""Phase 1 — Admin billing read."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.restaurant import BillingOut

router = APIRouter()


@router.get("/billing")
def my_billing(
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    restaurant = db.get(Restaurant, current.restaurant_id)
    if restaurant is None:
        raise NotFoundError("Restaurant not found.")
    billing = BillingOut(
        restaurant_id=restaurant.id,
        plan_tier=restaurant.plan_tier,
        plan_amount=restaurant.plan_amount,
        next_billing_date=restaurant.next_billing_date,
        invoices=[],
    )
    return ok(billing.model_dump(mode="json"))
