"""Phase 5 — Branch portal API package."""
from fastapi import APIRouter

from app.api.v1.branch import (
    customers,
    deliveries,
    devices,
    inventory,
    kitchens,
    orders,
    printing,
    requests,
    sub_kitchen,
    users,
)

router = APIRouter(prefix="/branch", tags=["branch"])
router.include_router(users.router)
router.include_router(devices.router)
router.include_router(requests.router)
router.include_router(kitchens.router)
router.include_router(deliveries.router)
router.include_router(inventory.router)
router.include_router(orders.router)
router.include_router(customers.router)
router.include_router(sub_kitchen.router)
router.include_router(printing.router)
