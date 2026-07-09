import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas.inventory import LowStockRead, StockLedgerRead
from services.inventory import get_low_stock, get_stock_ledger

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


@router.get(
    "/low-stock",
    response_model=LowStockRead,
    summary="Compare on-hand stock to reorder levels",
)
def get_inventory_low_stock(
    organization_id: uuid.UUID = Query(..., description="Organization scope"),
    branch_id: Optional[uuid.UUID] = Query(
        default=None, description="Optional branch filter"
    ),
    product_id: Optional[uuid.UUID] = Query(
        default=None, description="Optional product filter"
    ),
    low_stock_only: bool = Query(
        default=False,
        description="Return only products at or below their reorder level",
    ),
    db: Session = Depends(get_db),
) -> LowStockRead:
    return get_low_stock(
        db,
        organization_id=organization_id,
        branch_id=branch_id,
        product_id=product_id,
        low_stock_only=low_stock_only,
    )
