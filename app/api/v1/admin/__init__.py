"""Phase 2 — Admin portal API package."""
from fastapi import APIRouter

from app.api.v1.admin import (
    billing,
    inventory,
    locations,
    pricing,
    reads,
    requests,
    sales,
    settings,
    users,
)

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(billing.router)
router.include_router(locations.router)
router.include_router(users.router)
# pricing owns the literal /products/pricing — keep it ahead of any future
# /products/{id} route or that path parameter will shadow it.
router.include_router(pricing.router)
router.include_router(inventory.router)
router.include_router(requests.router)
router.include_router(reads.router)
router.include_router(settings.router)
router.include_router(sales.router)
