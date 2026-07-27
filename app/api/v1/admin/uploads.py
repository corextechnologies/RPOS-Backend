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
_EMPLOYEE_IMAGE_DIR = "employee-images"


async def _store_upload(file: UploadFile, request: Request, subdir: str) -> str:
    """Validate, persist one image under `subdir`, and return its public URL.

    Shared by every admin image upload so type/size limits and the on-disk
    layout stay identical across menu, employee, and any future uploads.
    """
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

    dest_dir = Path(settings.upload_dir) / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / filename).write_bytes(data)

    return str(request.base_url).rstrip("/") + f"/uploads/{subdir}/{filename}"


@router.post("/upload/menu-image")
async def upload_menu_image(
    file: UploadFile,
    request: Request,
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    url = await _store_upload(file, request, _MENU_IMAGE_DIR)
    return ok({"url": url})


@router.post("/upload/employee-image")
async def upload_employee_image(
    file: UploadFile,
    request: Request,
    current: User = Depends(require_role(UserRole.ADMIN)),
):
    """Upload an employee profile picture. Returns a URL to pass as image_url
    on POST/PATCH /admin/users."""
    url = await _store_upload(file, request, _EMPLOYEE_IMAGE_DIR)
    return ok({"url": url})
