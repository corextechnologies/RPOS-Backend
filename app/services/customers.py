"""Branch customer records — tenant-scoped, minimal."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps.scoping import apply_tenant_scope
from app.models.customer import Customer
from app.models.user import User
from app.schemas.branch import CustomerCreate
from app.services.audit import AuditService


class CustomerService:
    @staticmethod
    def create(db: Session, actor: User, body: CustomerCreate) -> Customer:
        customer = Customer(
            restaurant_id=actor.restaurant_id,
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
        )
        db.commit()
        db.refresh(customer)
        return customer

    @staticmethod
    def list(
        db: Session, actor: User, *, offset: int, limit: int
    ) -> tuple[list[Customer], int]:
        stmt = apply_tenant_scope(select(Customer), actor, Customer).order_by(
            Customer.id.desc()
        )
        total = db.execute(
            select(func.count()).select_from(stmt.subquery())
        ).scalar_one()
        rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
        return list(rows), total
