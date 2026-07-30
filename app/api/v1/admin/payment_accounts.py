"""Admin CRUD for payment accounts (ONLINE pay-into-account destinations).

The device caches the active ones via GET /pos/config so a cashier can show the
account (or a QR) even offline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.payment_accounts import (
    PaymentAccountIn,
    PaymentAccountOut,
    PaymentAccountUpdate,
)
from app.services.payment_accounts import PaymentAccountService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


def _dump(account) -> dict:
    return PaymentAccountOut.model_validate(account).model_dump(mode="json")


@router.post("/payment-accounts")
def create_account(
    body: PaymentAccountIn,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    account = PaymentAccountService.create(db, current, body)
    return ok(_dump(account))


@router.get("/payment-accounts")
def list_accounts(
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    rows = PaymentAccountService.list(db, current.restaurant_id)
    return ok([_dump(a) for a in rows])


@router.patch("/payment-accounts/{account_id}")
def update_account(
    account_id: int,
    body: PaymentAccountUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    account = PaymentAccountService.update(db, current, account_id, body)
    return ok(_dump(account))


@router.delete("/payment-accounts/{account_id}")
def delete_account(
    account_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    PaymentAccountService.delete(db, current, account_id)
    return ok({"id": account_id, "deleted": True})
