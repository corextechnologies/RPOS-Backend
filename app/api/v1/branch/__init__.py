"""Phase 5 — Branch portal API package."""
from fastapi import APIRouter

from app.api.v1.branch import requests

router = APIRouter(prefix="/branch", tags=["branch"])
router.include_router(requests.router)
