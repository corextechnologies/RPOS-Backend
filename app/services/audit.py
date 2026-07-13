"""Append-only audit trail service."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.user import User


class AuditService:
    @staticmethod
    def record(
        db: Session,
        *,
        actor: User,
        action: str,
        entity_type: str,
        entity_id: int,
        restaurant_id: int | None = None,
        payload: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            restaurant_id=restaurant_id,
            actor_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        db.add(entry)
        db.flush()
        return entry
