"""POS API package. Additive to /v1 — no fork of auth, users, or products."""
from fastapi import APIRouter

from app.api.v1.pos import integration, menu, money, orders, session

router = APIRouter(prefix="/pos", tags=["pos"])
router.include_router(session.router)
router.include_router(menu.router)
# money owns the literal /orders/flagged — keep it ahead of orders' /orders/{id}
# or that path parameter will shadow it.
router.include_router(money.router)
router.include_router(orders.router)
router.include_router(integration.router)
