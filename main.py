from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from routers.goods_receipts import router as goods_receipts_router
from routers.inventory import router as inventory_router
from routers.purchase_orders import router as purchase_orders_router

app = FastAPI(title="RPOS Backend")

app.include_router(purchase_orders_router)
app.include_router(goods_receipts_router)
app.include_router(inventory_router)


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
