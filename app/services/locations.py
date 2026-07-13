"""Admin location management service."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.deps.scoping import apply_tenant_scope
from app.models.location import Branch, Kitchen, Warehouse
from app.models.restaurant import Restaurant
from app.models.user import User
from app.schemas.admin import LocationCreate, LocationOut
from app.services.audit import AuditService

_LOCATION_MODELS = {
    "branch": Branch,
    "kitchen": Kitchen,
    "warehouse": Warehouse,
}


class LocationService:
    @staticmethod
    def create_branch(db: Session, admin: User, body: LocationCreate) -> Branch:
        restaurant = db.get(Restaurant, admin.restaurant_id)
        if restaurant is None:
            raise NotFoundError("Restaurant not found.")
        if restaurant.branch_limit is not None:
            count = db.execute(
                select(func.count())
                .select_from(Branch)
                .where(Branch.restaurant_id == admin.restaurant_id)
            ).scalar_one()
            if count >= restaurant.branch_limit:
                raise ConflictError(
                    "Branch limit reached for this restaurant plan.",
                    code="branch_limit_reached",
                )
        branch = Branch(
            restaurant_id=admin.restaurant_id,
            name=body.name,
            location=body.location,
        )
        db.add(branch)
        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="location.branch.create",
            entity_type="branch",
            entity_id=branch.id,
            restaurant_id=admin.restaurant_id,
        )
        db.commit()
        db.refresh(branch)
        return branch

    @staticmethod
    def create_kitchen(db: Session, admin: User, body: LocationCreate) -> Kitchen:
        kitchen = Kitchen(
            restaurant_id=admin.restaurant_id,
            name=body.name,
            location=body.location,
        )
        db.add(kitchen)
        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="location.kitchen.create",
            entity_type="kitchen",
            entity_id=kitchen.id,
            restaurant_id=admin.restaurant_id,
        )
        db.commit()
        db.refresh(kitchen)
        return kitchen

    @staticmethod
    def create_warehouse(db: Session, admin: User, body: LocationCreate) -> Warehouse:
        warehouse = Warehouse(
            restaurant_id=admin.restaurant_id,
            name=body.name,
            location=body.location,
        )
        db.add(warehouse)
        db.flush()
        AuditService.record(
            db,
            actor=admin,
            action="location.warehouse.create",
            entity_type="warehouse",
            entity_id=warehouse.id,
            restaurant_id=admin.restaurant_id,
        )
        db.commit()
        db.refresh(warehouse)
        return warehouse

    @staticmethod
    def list_branches(db: Session, admin: User) -> list[Branch]:
        stmt = apply_tenant_scope(select(Branch), admin, Branch).order_by(Branch.id)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_kitchens(db: Session, admin: User) -> list[Kitchen]:
        stmt = apply_tenant_scope(select(Kitchen), admin, Kitchen).order_by(Kitchen.id)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_warehouses(db: Session, admin: User) -> list[Warehouse]:
        stmt = apply_tenant_scope(select(Warehouse), admin, Warehouse).order_by(
            Warehouse.id
        )
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def to_out(location) -> LocationOut:
        return LocationOut.model_validate(location)
