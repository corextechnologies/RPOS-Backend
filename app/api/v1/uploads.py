"""Staff document uploads, shared by every portal.

One route rather than four near-identical ones: any manager who may create staff
may attach that staff member's photos, so the permission surface is unchanged and
there is a single place the CNIC sizing rule lives.

Always the PRIVATE bucket — every image here is a photo of a person or a national
ID document, and is only ever served as a short-lived signed link.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, UploadFile

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.services import storage

router = APIRouter(prefix="/uploads", tags=["uploads"])

_MANAGER_ROLES = (
    UserRole.ADMIN,
    UserRole.BRANCH_MANAGER,
    UserRole.WAREHOUSE_MANAGER,
    UserRole.KITCHEN_MANAGER,
)

# kind -> (folder, max_px, quality).
#
# A CNIC is capped far above an avatar and at higher quality on purpose: shrunk to
# avatar size the ID number stops being readable, which defeats the point of
# holding the document at all.
_KINDS = {
    "personal": ("staff-photos", settings.staff_image_max_px, settings.image_webp_quality),
    "cnic": ("staff-cnic", settings.cnic_image_max_px, settings.cnic_webp_quality),
}


@router.post("/staff-document")
async def upload_staff_document(
    file: UploadFile,
    kind: str = Form(...),
    current: User = Depends(require_role(*_MANAGER_ROLES)),
):
    """Upload a staff photo or CNIC scan. Returns `key` (persist this) and `url`.

    `kind` is `personal` or `cnic` and decides the size/quality applied.
    """
    if kind not in _KINDS:
        raise ConflictError(
            f"Unknown kind: {kind}. Use 'personal' or 'cnic'.",
            code="invalid_document_kind",
        )
    folder, max_px, quality = _KINDS[kind]
    key = storage.upload(
        await file.read(),
        file.content_type,
        folder=folder,
        public=False,
        max_px=max_px,
        quality=quality,
    )
    return ok({"key": key, "url": storage.resolve(key, public=False)})
