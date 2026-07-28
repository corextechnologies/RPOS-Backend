"""Admin file uploads — menu images, restaurant logo, employee photos.

Every endpoint returns BOTH `key` and `url`. Persist the `key`; the `url` is for
an immediate preview. Callers that post the `url` straight back are fine too —
storage.to_key() normalises it before it reaches the database.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile

from app.core.config import settings
from app.core.responses import ok
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.services import storage

router = APIRouter()

_MENU_IMAGE_DIR = "menu-images"
_EMPLOYEE_IMAGE_DIR = "employee-images"
_LOGO_IMAGE_DIR = "logos"


def _result(key: str, *, public: bool) -> dict:
    return {"key": key, "url": storage.resolve(key, public=public)}


@router.post("/upload/menu-image")
async def upload_menu_image(
    file: UploadFile,
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    """Menu photo — PUBLIC: anonymous QR-menu customers must be able to load it."""
    key = storage.upload(
        await file.read(),
        file.content_type,
        folder=_MENU_IMAGE_DIR,
        public=True,
        max_px=settings.menu_image_max_px,
        quality=settings.image_webp_quality,
    )
    return ok(_result(key, public=True))


@router.post("/upload/image")
async def upload_image(
    file: UploadFile,
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    """Restaurant logo — PUBLIC, for the same reason as menu photos.

    Public despite being branding rather than content: it renders on the public QR
    menu, so a private (expiring, uncacheable) link would slow every menu view and
    break on any page held open past the TTL — for no privacy gain.
    """
    key = storage.upload(
        await file.read(),
        file.content_type,
        folder=_LOGO_IMAGE_DIR,
        public=True,
        max_px=settings.menu_image_max_px,
        quality=settings.image_webp_quality,
    )
    return ok(_result(key, public=True))


@router.post("/upload/employee-image")
async def upload_employee_image(
    file: UploadFile,
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    """Employee photo — PRIVATE. A photo of a person is never publicly readable."""
    key = storage.upload(
        await file.read(),
        file.content_type,
        folder=_EMPLOYEE_IMAGE_DIR,
        public=False,
        max_px=settings.staff_image_max_px,
        quality=settings.image_webp_quality,
    )
    return ok(_result(key, public=False))
