"""Kitchen file uploads — sub-staff profile pictures."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, UploadFile

from app.api.v1.admin.uploads import _store_upload
from app.core.responses import ok
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User

router = APIRouter()

_STAFF_IMAGE_DIR = "staff-images"


@router.post("/upload/staff-image")
async def upload_staff_image(
    file: UploadFile,
    request: Request,
    current: User = Depends(require_role(UserRole.KITCHEN_MANAGER)),
):
    """Upload a kitchen sub-staff photo. Returns a URL to pass as image_url
    on POST/PATCH /kitchen/users.

    Its own route rather than reusing the admin one, which is ADMIN-only: a
    kitchen manager must not need admin rights to add a photo to their own
    roster. Storage rules (type, size, layout) are shared via _store_upload.
    """
    url = await _store_upload(file, request, _STAFF_IMAGE_DIR)
    return ok({"url": url})
