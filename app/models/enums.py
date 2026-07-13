import enum


class RestaurantStatus(str, enum.Enum):
    """A halted restaurant's users are blocked at the auth layer."""

    ACTIVE = "ACTIVE"
    HALTED = "HALTED"


class UserRole(str, enum.Enum):
    """The five primary portal roles. Sub-roles are added in later phases."""

    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    WAREHOUSE_MANAGER = "WAREHOUSE_MANAGER"
    KITCHEN_MANAGER = "KITCHEN_MANAGER"
    BRANCH_MANAGER = "BRANCH_MANAGER"

    @property
    def is_manager(self) -> bool:
        return self in {
            UserRole.WAREHOUSE_MANAGER,
            UserRole.KITCHEN_MANAGER,
            UserRole.BRANCH_MANAGER,
        }
