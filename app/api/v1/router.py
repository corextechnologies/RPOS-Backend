"""Aggregate v1 router — mount new phase routers here."""
from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    branch,
    kitchen,
    notifications,
    pos,
    public,
    requests,
    sub_kitchen,
    super_admin,
    uploads,
    users,
    warehouse,
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(super_admin.router)
api_router.include_router(admin.router)
api_router.include_router(warehouse.router)
api_router.include_router(kitchen.router)
api_router.include_router(branch.router)
# The sub-kitchen is its own portal (/v1/sub-kitchen/*), alongside kitchen and
# warehouse. It is ALSO dual-mounted under /v1/branch/sub-kitchen/* during the
# frontend migration — see app/api/v1/branch/__init__.py.
api_router.include_router(sub_kitchen.router)
api_router.include_router(pos.router)
api_router.include_router(requests.router)
api_router.include_router(notifications.router)
api_router.include_router(public.router)
api_router.include_router(uploads.router)
