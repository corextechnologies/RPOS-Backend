"""Central request state-machine rules — portals must never duplicate this."""
from __future__ import annotations

from app.core.exceptions import ConflictError, ForbiddenError
from app.models.enums import UserRole
from app.models.request_enums import (
    AdminToSuperAdminStatus,
    BranchToAdminStatus,
    KitchenToWarehouseStatus,
    RequestType,
    WarehouseToAdminStatus,
)

ALLOWED_TRANSITIONS: dict[RequestType, dict[str, set[str]]] = {
    RequestType.KITCHEN_TO_WAREHOUSE: {
        KitchenToWarehouseStatus.PENDING.value: {
            KitchenToWarehouseStatus.APPROVED.value,
        },
        KitchenToWarehouseStatus.APPROVED.value: {
            KitchenToWarehouseStatus.DISPATCHED.value,
        },
        KitchenToWarehouseStatus.DISPATCHED.value: {
            KitchenToWarehouseStatus.RECEIVED.value,
        },
        KitchenToWarehouseStatus.RECEIVED.value: set(),
    },
    RequestType.WAREHOUSE_TO_ADMIN_PO: {
        WarehouseToAdminStatus.PENDING.value: {
            WarehouseToAdminStatus.APPROVED.value,
            WarehouseToAdminStatus.PARTIALLY_APPROVED.value,
        },
        WarehouseToAdminStatus.APPROVED.value: {
            WarehouseToAdminStatus.DISPATCHED.value,
        },
        WarehouseToAdminStatus.PARTIALLY_APPROVED.value: {
            WarehouseToAdminStatus.DISPATCHED.value,
        },
        WarehouseToAdminStatus.DISPATCHED.value: {
            # Either the delivery was clean, or it wasn't.
            WarehouseToAdminStatus.RECEIVED.value,
            WarehouseToAdminStatus.REPORTED.value,
        },
        WarehouseToAdminStatus.REPORTED.value: {
            # Admin either sends the missing goods again, or accepts the report.
            WarehouseToAdminStatus.DISPATCHED.value,
            WarehouseToAdminStatus.RESOLVED.value,
        },
        WarehouseToAdminStatus.RESOLVED.value: {
            WarehouseToAdminStatus.RECEIVED.value,
        },
        WarehouseToAdminStatus.RECEIVED.value: set(),
    },
    RequestType.BRANCH_TO_ADMIN: {
        BranchToAdminStatus.PENDING.value: {
            BranchToAdminStatus.APPROVED.value,
            BranchToAdminStatus.REJECTED.value,
            BranchToAdminStatus.PARTIALLY_APPROVED.value,
        },
        BranchToAdminStatus.APPROVED.value: {
            BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
        },
        BranchToAdminStatus.PARTIALLY_APPROVED.value: {
            BranchToAdminStatus.FORWARDED_TO_KITCHEN.value,
        },
        BranchToAdminStatus.FORWARDED_TO_KITCHEN.value: {
            BranchToAdminStatus.IN_PRODUCTION.value,
        },
        BranchToAdminStatus.IN_PRODUCTION.value: {
            BranchToAdminStatus.PRODUCED.value,
        },
        BranchToAdminStatus.PRODUCED.value: {
            BranchToAdminStatus.ALLOCATED.value,
        },
        BranchToAdminStatus.ALLOCATED.value: {
            BranchToAdminStatus.RECEIVED.value,
        },
        BranchToAdminStatus.REJECTED.value: set(),
        BranchToAdminStatus.RECEIVED.value: set(),
    },
    RequestType.ADMIN_TO_SUPERADMIN_PLAN: {
        AdminToSuperAdminStatus.PENDING.value: {
            AdminToSuperAdminStatus.APPROVED.value,
            AdminToSuperAdminStatus.PARTIALLY_APPROVED.value,
            AdminToSuperAdminStatus.REJECTED.value,
        },
        AdminToSuperAdminStatus.APPROVED.value: {
            AdminToSuperAdminStatus.APPLIED.value,
        },
        AdminToSuperAdminStatus.PARTIALLY_APPROVED.value: {
            AdminToSuperAdminStatus.APPLIED.value,
        },
        AdminToSuperAdminStatus.APPLIED.value: {
            AdminToSuperAdminStatus.CONFIRMED.value,
        },
        AdminToSuperAdminStatus.REJECTED.value: set(),
        AdminToSuperAdminStatus.CONFIRMED.value: set(),
    },
}

# Who may *create* each request type.
CREATE_ROLES: dict[RequestType, set[UserRole]] = {
    RequestType.KITCHEN_TO_WAREHOUSE: {UserRole.KITCHEN_MANAGER},
    RequestType.WAREHOUSE_TO_ADMIN_PO: {UserRole.WAREHOUSE_MANAGER},
    RequestType.BRANCH_TO_ADMIN: {UserRole.BRANCH_MANAGER},
    RequestType.ADMIN_TO_SUPERADMIN_PLAN: {UserRole.ADMIN},
}

# Who may transition *to* each target status.
TRANSITION_ROLES: dict[RequestType, dict[str, set[UserRole]]] = {
    RequestType.KITCHEN_TO_WAREHOUSE: {
        KitchenToWarehouseStatus.APPROVED.value: {UserRole.WAREHOUSE_MANAGER},
        KitchenToWarehouseStatus.DISPATCHED.value: {UserRole.WAREHOUSE_MANAGER},
        KitchenToWarehouseStatus.RECEIVED.value: {UserRole.KITCHEN_MANAGER},
    },
    RequestType.WAREHOUSE_TO_ADMIN_PO: {
        WarehouseToAdminStatus.APPROVED.value: {UserRole.ADMIN},
        WarehouseToAdminStatus.PARTIALLY_APPROVED.value: {UserRole.ADMIN},
        WarehouseToAdminStatus.DISPATCHED.value: {UserRole.ADMIN},
        WarehouseToAdminStatus.RESOLVED.value: {UserRole.ADMIN},
        WarehouseToAdminStatus.REPORTED.value: {UserRole.WAREHOUSE_MANAGER},
        WarehouseToAdminStatus.RECEIVED.value: {UserRole.WAREHOUSE_MANAGER},
    },
    RequestType.BRANCH_TO_ADMIN: {
        BranchToAdminStatus.APPROVED.value: {UserRole.ADMIN},
        BranchToAdminStatus.REJECTED.value: {UserRole.ADMIN},
        BranchToAdminStatus.PARTIALLY_APPROVED.value: {UserRole.ADMIN},
        BranchToAdminStatus.FORWARDED_TO_KITCHEN.value: {UserRole.ADMIN},
        BranchToAdminStatus.IN_PRODUCTION.value: {UserRole.KITCHEN_MANAGER},
        BranchToAdminStatus.PRODUCED.value: {UserRole.KITCHEN_MANAGER},
        BranchToAdminStatus.ALLOCATED.value: {UserRole.KITCHEN_MANAGER},
        BranchToAdminStatus.RECEIVED.value: {UserRole.BRANCH_MANAGER},
    },
    RequestType.ADMIN_TO_SUPERADMIN_PLAN: {
        AdminToSuperAdminStatus.APPROVED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.PARTIALLY_APPROVED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.REJECTED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.APPLIED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.CONFIRMED.value: {UserRole.ADMIN},
    },
}

PARTIAL_APPROVAL_TYPES: set[RequestType] = {
    RequestType.WAREHOUSE_TO_ADMIN_PO,
    RequestType.BRANCH_TO_ADMIN,
    RequestType.ADMIN_TO_SUPERADMIN_PLAN,
}

PARTIAL_APPROVAL_STATUSES: set[str] = {
    WarehouseToAdminStatus.PARTIALLY_APPROVED.value,
    BranchToAdminStatus.PARTIALLY_APPROVED.value,
    AdminToSuperAdminStatus.PARTIALLY_APPROVED.value,
}

FULL_APPROVAL_STATUSES: set[str] = {
    WarehouseToAdminStatus.APPROVED.value,
    BranchToAdminStatus.APPROVED.value,
    AdminToSuperAdminStatus.APPROVED.value,
}


def supports_partial_approval(request_type: RequestType) -> bool:
    return request_type in PARTIAL_APPROVAL_TYPES


def validate_transition(
    request_type: RequestType, from_status: str, to_status: str
) -> None:
    type_map = ALLOWED_TRANSITIONS.get(request_type, {})
    allowed = type_map.get(from_status, set())
    if to_status not in allowed:
        raise ConflictError(
            f"Cannot transition from {from_status} to {to_status}.",
            code="invalid_transition",
        )


def roles_allowed_to_create(request_type: RequestType) -> set[UserRole]:
    return CREATE_ROLES.get(request_type, set())


def roles_allowed_to_transition(
    request_type: RequestType, to_status: str
) -> set[UserRole]:
    return TRANSITION_ROLES.get(request_type, {}).get(to_status, set())


def assert_role_can_create(actor_role: UserRole, request_type: RequestType) -> None:
    if actor_role not in roles_allowed_to_create(request_type):
        raise ForbiddenError("You are not allowed to create this request type.")


def assert_role_can_transition(
    actor_role: UserRole, request_type: RequestType, to_status: str
) -> None:
    allowed = roles_allowed_to_transition(request_type, to_status)
    if actor_role not in allowed:
        raise ForbiddenError("You are not allowed to perform this transition.")
