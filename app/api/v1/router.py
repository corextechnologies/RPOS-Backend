"""Aggregate v1 router — mount new phase routers here."""
from fastapi import APIRouter

from app.api.v1 import admin, auth, notifications, requests, super_admin, users

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(super_admin.router)
api_router.include_router(admin.router)
api_router.include_router(requests.router)
api_router.include_router(notifications.router)
