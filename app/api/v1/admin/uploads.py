"""Admin file uploads — menu images, etc."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User

router = APIRouter()

_ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_MENU_IMAGE_DIR = "menu-images"


@router.post("/upload/menu-image")
async def upload_menu_image(
    file: UploadFile,
    request: Request,
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    if file.content_type not in _ALLOWED_TYPES:
        raise ConflictError(
            f"Unsupported file type: {file.content_type}. "
            f"Accepted: JPEG, PNG, WebP.",
            code="invalid_file_type",
        )

    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise ConflictError(
            f"File too large ({len(data)} bytes). Maximum is {settings.max_upload_bytes} bytes.",
            code="file_too_large",
        )

    ext = _ALLOWED_TYPES[file.content_type]
    filename = f"{uuid.uuid4().hex}{ext}"

    dest_dir = Path(settings.upload_dir) / _MENU_IMAGE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / filename).write_bytes(data)

    url = str(request.base_url).rstrip("/") + f"/uploads/{_MENU_IMAGE_DIR}/{filename}"
    return ok({"url": url})
