from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from database import get_db
from schemas.goods_receipts import GoodsReceiptCreate, GoodsReceiptRead
from services.goods_receipts import create_goods_receipt

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
