"""Admin self-service settings: display name and profile picture."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.admin import AdminProfileOut, AdminProfileUpdate
from app.services.audit import AuditService


class SettingsService:
    @staticmethod
    def get_profile(admin: User) -> AdminProfileOut:
        return AdminProfileOut.model_validate(admin)

    @staticmethod
    def update_profile(
        db: Session, admin: User, body: AdminProfileUpdate
    ) -> AdminProfileOut:
        changes = body.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(admin, field, value)
        AuditService.record(
            db,
            actor=admin,
            action="admin.settings.update",
            entity_type="user",
            entity_id=admin.id,
            restaurant_id=admin.restaurant_id,
            payload=changes or None,
        )
        db.commit()
        db.refresh(admin)
        return AdminProfileOut.model_validate(admin)
