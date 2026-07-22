import enum


class RestaurantStatus(str, enum.Enum):
    """A halted restaurant's users are blocked at the auth layer."""

    ACTIVE = "ACTIVE"
    HALTED = "HALTED"


class UserRole(str, enum.Enum):
    """The five primary portal roles, plus sub-roles added by later phases."""

    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    WAREHOUSE_MANAGER = "WAREHOUSE_MANAGER"
    KITCHEN_MANAGER = "KITCHEN_MANAGER"
    BRANCH_MANAGER = "BRANCH_MANAGER"
    # Phase 5: branch sub-staff (salesperson / cashier / order-taker via position).
    BRANCH_STAFF = "BRANCH_STAFF"

    @property
    def is_manager(self) -> bool:
        return self in {
            UserRole.WAREHOUSE_MANAGER,
            UserRole.KITCHEN_MANAGER,
            UserRole.BRANCH_MANAGER,
        }


class BranchPosition(str, enum.Enum):
    """Phase 5: the position of a branch sub-staff member (role stays BRANCH_STAFF)."""

    SALESPERSON = "SALESPERSON"
    CASHIER = "CASHIER"
    ORDER_TAKER = "ORDER_TAKER"
