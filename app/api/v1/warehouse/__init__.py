"""Phase 3 — Warehouse portal API package."""
from fastapi import APIRouter

from app.api.v1.warehouse import inventory, requests, users

router = APIRouter(prefix="/warehouse", tags=["warehouse"])
router.include_router(users.router)
router.include_router(inventory.router)
router.include_router(requests.router)
