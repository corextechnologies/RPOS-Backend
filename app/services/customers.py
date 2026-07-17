"""Branch customer records — scoped to a single branch."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.deps.scoping import apply_tenant_scope
from app.models.customer import Customer
from app.models.user import User
from app.schemas.branch import CustomerCreate, CustomerUpdate
from app.services.audit import AuditService


class CustomerService:
    @staticmethod
    def create(
        db: Session, actor: User, branch_id: int, body: CustomerCreate
    ) -> Customer:
        customer = Customer(
            restaurant_id=actor.restaurant_id,
            branch_id=branch_id,
            name=body.name,
            phone=body.phone,
        )
        db.add(customer)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="branch.customer.create",
            entity_type="customer",
            entity_id=customer.id,
            restaurant_id=actor.restaurant_id,
            payload={"branch_id": branch_id},
        )
        db.commit()
        db.refresh(customer)
        return customer

    @staticmethod
    def _visible(actor: User, branch_id: int):
        """Live customers at this branch only.

        Branch-scoped with strict equality — no `OR branch_id IS NULL` — so
        nothing leaks across branches. apply_tenant_scope stays as defence in
        depth (app/deps/scoping.py is the law).
        """
        return (
            apply_tenant_scope(select(Customer), actor, Customer)
            .where(Customer.branch_id == branch_id)
            .where(Customer.deleted_at.is_(None))
        )

    @staticmethod
    def list(
        db: Session,
        actor: User,
        branch_id: int,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
    ) -> tuple[list[Customer], int]:
        stmt = CustomerService._visible(actor, branch_id)
        if search:
            # Name or phone — the two things a counter has to hand. Phone is the
            # one that actually identifies someone.
            term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(Customer.phone.ilike(term), Customer.name.ilike(term))
            )
        stmt = stmt.order_by(Customer.id.desc())
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return list(rows), total

    @staticmethod
    def get(db: Session, actor: User, branch_id: int, customer_id: int) -> Customer:
        row = db.execute(
            CustomerService._visible(actor, branch_id).where(
                Customer.id == customer_id
            )
        ).scalar_one_or_none()
        if row is None:
            # 404 rather than 403 — never confirm another branch's customer.
            raise NotFoundError("Customer not found.")
        return row

    @staticmethod
    def update(
        db: Session, actor: User, branch_id: int, customer_id: int, body: CustomerUpdate
    ) -> Customer:
        customer = CustomerService.get(db, actor, branch_id, customer_id)
        fields = body.model_fields_set
        payload: dict = {}
        if "name" in fields and body.name is not None:
            customer.name = body.name
            payload["name"] = body.name
        if "phone" in fields:
            customer.phone = body.phone
            payload["phone"] = body.phone
        AuditService.record(
            db,
            actor=actor,
            action="branch.customer.update",
            entity_type="customer",
            entity_id=customer.id,
            restaurant_id=actor.restaurant_id,
            payload=payload,
        )
        db.commit()
        db.refresh(customer)
        return customer

    @staticmethod
    def soft_delete(
        db: Session, actor: User, branch_id: int, customer_id: int
    ) -> None:
        customer = CustomerService.get(db, actor, branch_id, customer_id)
        customer.deleted_at = datetime.now(timezone.utc)
        AuditService.record(
            db,
            actor=actor,
            action="branch.customer.delete",
            entity_type="customer",
            entity_id=customer.id,
            restaurant_id=actor.restaurant_id,
        )
        db.commit()
