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
    # Warehouse sub-staff created under a warehouse manager. Distinct from
    # WAREHOUSE_MANAGER purely so managers and their sub-staff are
    # distinguishable in the Admin roster and warehouse portal — operationally
    # they share the manager's warehouse access, minus staff management.
    WAREHOUSE_STAFF = "WAREHOUSE_STAFF"
    KITCHEN_STAFF = "KITCHEN_STAFF"

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
    # Sub-kitchen operator: runs the branch prep board (final prep / finishing).
    # Unlike kitchen/warehouse sub-staff (non-login roster records), a branch CHEF
    # logs in and operates a portal — the prep station — but never the sell floor
    # (no order-taking, no cash). Capabilities live in app/deps/capabilities.py.
    CHEF = "CHEF"
