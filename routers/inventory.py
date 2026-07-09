import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.inventory import StockLedgerRead
from services.inventory import get_stock_ledger

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get(
    "/stock",
    response_model=StockLedgerRead,
    summary="Live stock ledger by product and batch",
)
def get_inventory_stock(
    organization_id: uuid.UUID = Query(..., description="Organization scope"),
    branch_id: Optional[uuid.UUID] = Query(
        default=None, description="Optional branch filter"
    ),
    product_id: Optional[uuid.UUID] = Query(
        default=None, description="Optional product filter"
    ),
    db: Session = Depends(get_db),
) -> StockLedgerRead:
    return get_stock_ledger(
        db,
        organization_id=organization_id,
        branch_id=branch_id,
        product_id=product_id,
    )
