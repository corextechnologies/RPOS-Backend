"""Idempotent replay for mutating POS endpoints.

Why a service and not middleware: middleware runs outside the get_db dependency,
so it cannot share the request's transaction. The key would then commit
separately from the work it guards, and a crash between the two would leave a key
claimed for work that never happened — the exact failure the mechanism exists to
prevent. It also couldn't see the tenant without decoding the JWT a second time.

The claim protocol, all inside the caller's transaction:

  1. INSERT .. ON CONFLICT DO NOTHING RETURNING id.
  2. Row returned -> we own it -> run the work -> mark COMPLETED. The router's
     existing db.commit() commits the key and the order atomically.
  3. No row -> a concurrent duplicate. The INSERT already BLOCKED on the unique
     index until the first request committed or rolled back, so re-SELECT:
     COMPLETED -> replay the stored response; gone (first one rolled back) ->
     claim it ourselves.
  4. The work raises -> db.rollback() -> the key vanishes with it -> the client
     retries into a clean slate.

No reaper, no TTL sweeper, no IN_FLIGHT zombies. Blocking a duplicate for the
request duration is the correct answer to a double-tap anyway.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ConflictError
from app.models.pos import IdempotencyKey, IdempotencyStatus
from app.models.user import User


class IdempotencyConflictError(AppError):
    """Same key, different body — a client bug, not a retry."""

    status_code = 422
    code = "idempotency_key_reuse"


def hash_body(body: Any) -> str:
    """Stable hash of a request body. sort_keys so key order can't fork the hash."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class Slot:
    """The outcome of a claim: either replay the stored response, or do the work."""

    replay: bool
    response: Any = None
    response_status: int | None = None
    _row_id: int | None = None
    _db: Session | None = None

    def complete(self, response: Any, status: int = 200) -> None:
        """Record the result on the claimed key, in the caller's transaction."""
        if self.replay or self._row_id is None or self._db is None:
            return
        row = self._db.get(IdempotencyKey, self._row_id)
        if row is not None:
            row.status = IdempotencyStatus.COMPLETED
            row.response_body = response
            row.response_status = status
            row.completed_at = datetime.now(timezone.utc)
            self._db.flush()


class IdempotencyService:
    @staticmethod
    def claim(
        db: Session,
        actor: User,
        *,
        endpoint: str,
        key: str,
        body: Any,
        branch_id: int | None = None,
    ) -> Slot:
        request_hash = hash_body(body)

        claimed = db.execute(
            pg_insert(IdempotencyKey)
            .values(
                restaurant_id=actor.restaurant_id,
                branch_id=branch_id,
                key=key,
                endpoint=endpoint,
                request_hash=request_hash,
                status=IdempotencyStatus.IN_FLIGHT.value,
                actor_id=actor.id,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_scope_key")
            .returning(IdempotencyKey.id)
        ).scalar_one_or_none()

        if claimed is not None:
            return Slot(replay=False, _row_id=claimed, _db=db)

        # Someone else holds this key. Our INSERT blocked on the unique index
        # until they committed or rolled back, so whatever we read now is final.
        existing = db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.restaurant_id == actor.restaurant_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
        ).scalar_one_or_none()

        if existing is None:
            # The holder rolled back and took the key with it. Claim it now; if we
            # lose this race too, that is a genuine conflict worth surfacing.
            retried = db.execute(
                pg_insert(IdempotencyKey)
                .values(
                    restaurant_id=actor.restaurant_id,
                    branch_id=branch_id,
                    key=key,
                    endpoint=endpoint,
                    request_hash=request_hash,
                    status=IdempotencyStatus.IN_FLIGHT.value,
                    actor_id=actor.id,
                )
                .on_conflict_do_nothing(constraint="uq_idempotency_scope_key")
                .returning(IdempotencyKey.id)
            ).scalar_one_or_none()
            if retried is not None:
                return Slot(replay=False, _row_id=retried, _db=db)
            raise ConflictError(
                "This request is already in progress; retry shortly.",
                code="idempotency_in_flight",
            )

        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                "This Idempotency-Key was already used with a different request "
                "body. Use a fresh key for a new request."
            )

        if existing.status == IdempotencyStatus.COMPLETED:
            return Slot(
                replay=True,
                response=existing.response_body,
                response_status=existing.response_status or 200,
            )

        # Still IN_FLIGHT after the index let us through: the holder's transaction
        # is open. Tell the client to retry rather than duplicating the work.
        raise ConflictError(
            "This request is already in progress; retry shortly.",
            code="idempotency_in_flight",
        )
