"""Phase 4 — Cloud Kitchen portal API package."""
from fastapi import APIRouter, Depends

from app.api.v1.kitchen import (
    dispatch_notifications,
    inventory,
    locations,
    production,
    production_targets,
    requests,
    uploads,
    users,
)
from app.deps.auth import require_central_kitchen_enabled

# The whole kitchen domain is 403'd for a tenant that runs kitchen-off (F4). This
# backs the frontend's route guards server-side so a direct call can't bypass them.
router = APIRouter(
    prefix="/kitchen",
    tags=["kitchen"],
    dependencies=[Depends(require_central_kitchen_enabled)],
)
router.include_router(locations.router)
router.include_router(inventory.router)
router.include_router(requests.router)
router.include_router(dispatch_notifications.router)
# The kitchen's own catalogue, its recipes, and making things.
router.include_router(production.router)
router.include_router(production_targets.router)
router.include_router(users.router)
router.include_router(uploads.router)
