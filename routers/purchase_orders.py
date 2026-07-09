from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from database import get_db
from schemas.purchase_orders import PurchaseOrderCreate, PurchaseOrderRead
from services.purchase_orders import create_purchase_order

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


@router.post(
    "",
    response_model=PurchaseOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a purchase order with line items",
)
def post_purchase_order(
    payload: PurchaseOrderCreate,
    db: Session = Depends(get_db),
) -> PurchaseOrderRead:
    return create_purchase_order(db, payload)
