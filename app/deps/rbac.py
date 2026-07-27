"""Role-creation and location guards for portal user provisioning."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.enums import UserRole
from app.models.location import Branch, Kitchen, Warehouse
from app.models.user import User

ADMIN_CREATABLE_ROLES = {
    UserRole.WAREHOUSE_MANAGER,
    UserRole.KITCHEN_MANAGER,
    UserRole.BRANCH_MANAGER,
}

WAREHOUSE_CREATABLE_ROLES = {
    UserRole.WAREHOUSE_MANAGER,
}

# A branch manager creates branch sub-staff only — never peer managers.
BRANCH_CREATABLE_ROLES = {
    UserRole.BRANCH_STAFF,
}

# The roles that make up an Admin's employee roster: every operational role
# below the Admin owner (managers + branch sub-staff). Used as an explicit
# allowlist for the roster read so the query can never hydrate a row whose
# stored role is no longer in the Python enum — e.g. a legacy SUB_CHEF, which
# stays in the Postgres user_role type after the role was retired. A bare
# `role != ADMIN` filter would try to map that string back to UserRole and
# raise LookupError, 500-ing the whole list.
KITCHEN_CREATABLE_ROLES = {
    UserRole.KITCHEN_STAFF,
}

EMPLOYEE_ROSTER_ROLES = ADMIN_CREATABLE_ROLES | {
    UserRole.BRANCH_STAFF,
    UserRole.WAREHOUSE_STAFF,
    UserRole.KITCHEN_STAFF,
}

_LOCATION_MODELS = {
    UserRole.BRANCH_MANAGER: (Branch, "branch_id"),
    UserRole.KITCHEN_MANAGER: (Kitchen, "kitchen_id"),
    UserRole.WAREHOUSE_MANAGER: (Warehouse, "warehouse_id"),
    UserRole.BRANCH_STAFF: (Branch, "branch_id"),
    UserRole.KITCHEN_STAFF: (Kitchen, "kitchen_id"),
}

# Sub-staff are scoped to their LOCATION, not the manager who created them: any
# manager of a branch/kitchen/warehouse manages all sub-staff there. These maps
# turn a manager's role into the staff role they own and the location FK they
# share with that staff (mirrors the fk names in _LOCATION_MODELS).
MANAGER_STAFF_ROLE = {
    UserRole.BRANCH_MANAGER: UserRole.BRANCH_STAFF,
    UserRole.KITCHEN_MANAGER: UserRole.KITCHEN_STAFF,
    UserRole.WAREHOUSE_MANAGER: UserRole.WAREHOUSE_STAFF,
}
MANAGER_LOCATION_ATTR = {
    UserRole.BRANCH_MANAGER: "branch_id",
    UserRole.KITCHEN_MANAGER: "kitchen_id",
    UserRole.WAREHOUSE_MANAGER: "warehouse_id",
}

# Roles that live at a warehouse and may read/operate warehouse-scoped data.
# Both roles operate the warehouse; only the manager provisions sub-staff.
WAREHOUSE_ROLES = {UserRole.WAREHOUSE_MANAGER, UserRole.WAREHOUSE_STAFF}

# Roles that live at a kitchen and may read kitchen-scoped data.
KITCHEN_ROLES = {UserRole.KITCHEN_MANAGER, UserRole.KITCHEN_STAFF}

# Roles that live at a branch and may read branch-scoped data.
BRANCH_ROLES = {UserRole.BRANCH_MANAGER, UserRole.BRANCH_STAFF}


def assert_admin_can_create_role(target_role: UserRole) -> None:
    if target_role not in ADMIN_CREATABLE_ROLES:
        raise ForbiddenError(
            "Admin may only create Warehouse, Kitchen, or Branch managers."
        )


def validate_manager_location(
    db: Session,
    restaurant_id: int,
    role: UserRole,
    *,
    branch_id: int | None = None,
    kitchen_id: int | None = None,
    warehouse_id: int | None = None,
) -> None:
    """Ensure the manager role has the correct location FK for the restaurant."""
    location_map = {
        UserRole.BRANCH_MANAGER: branch_id,
        UserRole.KITCHEN_MANAGER: kitchen_id,
        UserRole.WAREHOUSE_MANAGER: warehouse_id,
        UserRole.BRANCH_STAFF: branch_id,
    }
    required_id = location_map.get(role)
    if required_id is None:
        raise ConflictError(
            f"{role.value} requires a location assignment.",
            code="missing_location",
        )

    model, _ = _LOCATION_MODELS[role]
    row = db.get(model, required_id)
    if row is None or row.restaurant_id != restaurant_id:
        raise NotFoundError("Location not found in this restaurant.")

    # Reject wrong FK fields for the role.
    if role == UserRole.BRANCH_MANAGER and (kitchen_id or warehouse_id):
        raise ConflictError("Branch manager must only set branch_id.")
    if role == UserRole.KITCHEN_MANAGER and (branch_id or warehouse_id):
        raise ConflictError("Kitchen manager must only set kitchen_id.")
    if role == UserRole.WAREHOUSE_MANAGER and (branch_id or kitchen_id):
        raise ConflictError("Warehouse manager must only set warehouse_id.")
    if role == UserRole.BRANCH_STAFF and (kitchen_id or warehouse_id):
        raise ConflictError("Branch staff must only set branch_id.")
    if role == UserRole.KITCHEN_STAFF and (branch_id or warehouse_id):
        raise ConflictError("Kitchen staff must only set kitchen_id.")


def assert_warehouse_manager_can_create_staff(actor: User) -> None:
    """Warehouse managers may only create peer warehouse staff under themselves."""
    if actor.role != UserRole.WAREHOUSE_MANAGER:
        raise ForbiddenError("Only warehouse managers can create warehouse staff.")
    if actor.warehouse_id is None:
        raise ConflictError(
            "Warehouse manager must be assigned to a warehouse.",
            code="missing_warehouse_assignment",
        )


def require_actor_warehouse_id(actor: User) -> int:
    """The caller's warehouse. Both warehouse roles read/operate; staff
    provisioning is gated separately to the manager."""
    if actor.role not in WAREHOUSE_ROLES:
        raise ForbiddenError("Warehouse access required.")
    if actor.warehouse_id is None:
        raise ConflictError(
            "Warehouse manager must be assigned to a warehouse.",
            code="missing_warehouse_assignment",
        )
    return actor.warehouse_id


def require_actor_branch_id(actor: User) -> int:
    """The caller's branch. Both branch roles read; writes are gated by role."""
    if actor.role not in BRANCH_ROLES:
        raise ForbiddenError("Branch access required.")
    if actor.branch_id is None:
        raise ConflictError(
            "Branch staff must be assigned to a branch.",
            code="missing_branch_assignment",
        )
    return actor.branch_id


def assert_branch_manager_can_create_staff(actor: User) -> None:
    """Branch managers may only create branch sub-staff under themselves."""
    if actor.role != UserRole.BRANCH_MANAGER:
        raise ForbiddenError("Only branch managers can create branch staff.")
    if actor.branch_id is None:
        raise ConflictError(
            "Branch manager must be assigned to a branch.",
            code="missing_branch_assignment",
        )


def assert_branch_can_create_role(target_role: UserRole) -> None:
    if target_role not in BRANCH_CREATABLE_ROLES:
        raise ForbiddenError("Branch managers may only create branch staff.")


def assert_kitchen_manager_can_create_staff(actor: User) -> None:
    """Kitchen managers may only create kitchen sub-staff under themselves."""
    if actor.role != UserRole.KITCHEN_MANAGER:
        raise ForbiddenError("Only kitchen managers can create kitchen staff.")
    if actor.kitchen_id is None:
        raise ConflictError(
            "Kitchen manager must be assigned to a kitchen.",
            code="missing_kitchen_assignment",
        )


def require_actor_kitchen_id(actor: User) -> int:
    """The caller's kitchen. Both kitchen roles read; writes are gated by role."""
    if actor.role not in KITCHEN_ROLES:
        raise ForbiddenError("Kitchen access required.")
    if actor.kitchen_id is None:
        raise ConflictError(
            "Kitchen staff must be assigned to a kitchen.",
            code="missing_kitchen_assignment",
        )
    return actor.kitchen_id
