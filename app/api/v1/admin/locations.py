"""Admin location write routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.admin import LocationCreate, LocationUpdate
from app.services.locations import LocationService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


@router.post("/branches")
def create_branch(
    body: LocationCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    branch = LocationService.create_branch(db, current, body)
    return ok(LocationService.to_out(branch).model_dump(mode="json"))


@router.post("/kitchens")
def create_kitchen(
    body: LocationCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    # A kitchen-off tenant may not create kitchens (F4). Guarding here keeps the
    # disable-guard's invariant true: a kitchen-off restaurant has no kitchens.
    restaurant = current.restaurant
    if restaurant is not None and getattr(
        restaurant, "has_central_kitchen", True
    ) is False:
        raise ConflictError(
            "This restaurant's central kitchen is disabled; enable it before "
            "adding a kitchen.",
            code="central_kitchen_disabled",
        )
    kitchen = LocationService.create_kitchen(db, current, body)
    return ok(LocationService.to_out(kitchen).model_dump(mode="json"))


@router.post("/warehouses")
def create_warehouse(
    body: LocationCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    warehouse = LocationService.create_warehouse(db, current, body)
    return ok(LocationService.to_out(warehouse).model_dump(mode="json"))


# --- edit / delete (branch, kitchen, warehouse share one implementation) ---

def _patch(kind: str):
    def _handler(
        location_id: int,
        body: LocationUpdate,
        current: User = Depends(require_role(UserRole.ADMIN)),
        db: Session = Depends(get_db),
    ):
        row = LocationService.update_location(db, current, kind, location_id, body)
        return ok(LocationService.to_out(row).model_dump(mode="json"))

    return _handler


def _delete(kind: str):
    def _handler(
        location_id: int,
        current: User = Depends(require_role(UserRole.ADMIN)),
        db: Session = Depends(get_db),
    ):
        LocationService.delete_location(db, current, kind, location_id)
        return ok({"detail": f"{kind.capitalize()} deleted."})

    return _handler


for _kind, _prefix in (("branch", "branches"), ("kitchen", "kitchens"),
                       ("warehouse", "warehouses")):
    router.add_api_route(
        f"/{_prefix}/{{location_id}}", _patch(_kind), methods=["PATCH"]
    )
    router.add_api_route(
        f"/{_prefix}/{{location_id}}", _delete(_kind), methods=["DELETE"]
    )
