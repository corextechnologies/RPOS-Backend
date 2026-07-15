"""Phase 5 — Branch portal API package."""
from fastapi import APIRouter

from app.api.v1.branch import customers, inventory, orders, requests, users

router = APIRouter(prefix="/branch", tags=["branch"])
router.include_router(users.router)
router.include_router(requests.router)
router.include_router(inventory.router)
router.include_router(orders.router)
router.include_router(customers.router)
