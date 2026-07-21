"""Phase 4 — Cloud Kitchen portal API package."""
from fastapi import APIRouter

from app.api.v1.kitchen import (
    dispatch_notifications,
    inventory,
    locations,
    production,
    production_targets,
    requests,
    users,
)

router = APIRouter(prefix="/kitchen", tags=["kitchen"])
router.include_router(users.router)
router.include_router(locations.router)
router.include_router(inventory.router)
router.include_router(requests.router)
router.include_router(dispatch_notifications.router)
# The kitchen's own catalogue, its recipes, and making things.
router.include_router(production.router)
router.include_router(production_targets.router)
