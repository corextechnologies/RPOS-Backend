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
    sub_kitchen_oversight,
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
# CUTOVER done: the full sub-kitchen station is /v1/sub-kitchen/* (see
# app/api/v1/router.py). What stays here at /v1/branch/sub-kitchen/* is the
# manager's READ-ONLY oversight tab — watch only, no operate handlers exist on it.
router.include_router(sub_kitchen_oversight.router)
router.include_router(printing.router)
