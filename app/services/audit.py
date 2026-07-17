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
        before: dict | None = None,
        after: dict | None = None,
        approved_by_id: int | None = None,
        terminal_id: int | None = None,
        reason_code: str | None = None,
    ) -> AuditLog:
        """Append one audit row.

        The POS-0 arguments are all optional so every existing caller is
        unchanged; money-touching POS actions fill them in (see the AuditLog
        docstring for why they are columns and not payload keys).
        """
        entry = AuditLog(
            restaurant_id=restaurant_id,
            actor_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            before=before,
            after=after,
            approved_by_id=approved_by_id,
            terminal_id=terminal_id,
            reason_code=reason_code,
        )
        db.add(entry)
        db.flush()
        return entry
