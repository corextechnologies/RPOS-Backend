"""Admin CRUD over payment accounts + the device-facing active-account query."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.location import Branch
from app.models.payment import PaymentAccount
from app.models.user import User
from app.services.audit import AuditService


class PaymentAccountService:
    @staticmethod
    def _own(db: Session, restaurant_id: int, account_id: int) -> PaymentAccount:
        account = db.get(PaymentAccount, account_id)
        if account is None or account.restaurant_id != restaurant_id:
            raise NotFoundError(
                "Payment account not found.", code="payment_account_not_found"
            )
        return account

    @staticmethod
    def _validate_branch(db: Session, restaurant_id: int, branch_id: int | None) -> None:
        if branch_id is None:
            return
        branch = db.get(Branch, branch_id)
        if branch is None or branch.restaurant_id != restaurant_id:
            raise NotFoundError("Branch not found.", code="branch_not_found")

    @staticmethod
    def create(db: Session, actor: User, body) -> PaymentAccount:
        PaymentAccountService._validate_branch(db, actor.restaurant_id, body.branch_id)
        account = PaymentAccount(
            restaurant_id=actor.restaurant_id,
            branch_id=body.branch_id,
            label=body.label,
            kind=body.kind,
            account_name=body.account_name,
            account_ref=body.account_ref,
            bank_or_wallet=body.bank_or_wallet,
            qr_payload=body.qr_payload,
            is_active=body.is_active,
            sort_order=body.sort_order,
        )
        db.add(account)
        db.flush()
        AuditService.record(
            db, actor=actor, action="admin.payment_account.create",
            entity_type="payment_account", entity_id=account.id,
            restaurant_id=actor.restaurant_id,
            after={"label": account.label, "kind": account.kind.value,
                   "branch_id": account.branch_id},
        )
        db.commit()
        db.refresh(account)
        return account

    @staticmethod
    def list(db: Session, restaurant_id: int) -> list[PaymentAccount]:
        return list(
            db.execute(
                select(PaymentAccount)
                .where(PaymentAccount.restaurant_id == restaurant_id)
                .order_by(PaymentAccount.sort_order, PaymentAccount.id)
            ).scalars()
        )

    @staticmethod
    def update(db: Session, actor: User, account_id: int, body) -> PaymentAccount:
        account = PaymentAccountService._own(db, actor.restaurant_id, account_id)
        fields = body.model_dump(exclude_unset=True)
        if "branch_id" in fields:
            PaymentAccountService._validate_branch(db, actor.restaurant_id, fields["branch_id"])
        for key, value in fields.items():
            setattr(account, key, value)
        db.flush()
        AuditService.record(
            db, actor=actor, action="admin.payment_account.update",
            entity_type="payment_account", entity_id=account.id,
            restaurant_id=actor.restaurant_id, after=fields,
        )
        db.commit()
        db.refresh(account)
        return account

    @staticmethod
    def delete(db: Session, actor: User, account_id: int) -> None:
        account = PaymentAccountService._own(db, actor.restaurant_id, account_id)
        db.delete(account)
        AuditService.record(
            db, actor=actor, action="admin.payment_account.delete",
            entity_type="payment_account", entity_id=account_id,
            restaurant_id=actor.restaurant_id, before={"label": account.label},
        )
        db.commit()

    @staticmethod
    def active_for_branch(
        db: Session, restaurant_id: int, branch_id: int
    ) -> list[PaymentAccount]:
        """Active accounts available at this branch — its own plus the
        every-branch (branch_id NULL) ones. Used by GET /pos/config."""
        return list(
            db.execute(
                select(PaymentAccount)
                .where(
                    PaymentAccount.restaurant_id == restaurant_id,
                    PaymentAccount.is_active.is_(True),
                    or_(
                        PaymentAccount.branch_id == branch_id,
                        PaymentAccount.branch_id.is_(None),
                    ),
                )
                .order_by(PaymentAccount.sort_order, PaymentAccount.id)
            ).scalars()
        )
