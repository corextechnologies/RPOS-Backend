"""Phase 4 — Cloud Kitchen portal API package."""
from fastapi import APIRouter

from app.api.v1.kitchen import inventory, requests, users

router = APIRouter(prefix="/kitchen", tags=["kitchen"])
router.include_router(users.router)
router.include_router(inventory.router)
router.include_router(requests.router)
