"""Kitchen file uploads — sub-staff profile pictures."""
from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile

from app.core.config import settings
from app.core.responses import ok
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.services import storage

router = APIRouter()

_STAFF_IMAGE_DIR = "staff-images"


@router.post("/upload/staff-image")
async def upload_staff_image(
    file: UploadFile,
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
):
    """Kitchen sub-staff photo — PRIVATE. Returns `key` (persist this) and `url`.

    Its own route rather than the ADMIN-only one: a kitchen manager must not need
    admin rights to add a photo to their own roster.
    """
    key = storage.upload(
        await file.read(),
        file.content_type,
        folder=_STAFF_IMAGE_DIR,
        public=False,
        max_px=settings.staff_image_max_px,
        quality=settings.image_webp_quality,
    )
    return ok({"key": key, "url": storage.resolve(key, public=False)})
