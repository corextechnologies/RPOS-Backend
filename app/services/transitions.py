"""Central request state-machine rules — portals must never duplicate this."""
from __future__ import annotations

from app.core.exceptions import ConflictError, ForbiddenError
from app.models.enums import UserRole
from app.models.request_enums import (
    AdminToSuperAdminStatus,
    BranchToAdminStatus,
    KitchenToAdminStatus,
    KitchenToWarehouseStatus,
    RequestType,
    WarehouseToAdminStatus,
)

ALLOWED_TRANSITIONS: dict[RequestType, dict[str, set[str]]] = {
    RequestType.KITCHEN_TO_WAREHOUSE: {
        KitchenToWarehouseStatus.PENDING.value: {
            KitchenToWarehouseStatus.APPROVED.value,
            KitchenToWarehouseStatus.PARTIALLY_APPROVED.value,
        },
        KitchenToWarehouseStatus.APPROVED.value: {
            KitchenToWarehouseStatus.DISPATCHED.value,
        },
        KitchenToWarehouseStatus.PARTIALLY_APPROVED.value: {
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
            BranchToAdminStatus.DISPATCHED.value,
        },
        BranchToAdminStatus.DISPATCHED.value: {
            BranchToAdminStatus.RECEIVED.value,
        },
        BranchToAdminStatus.REJECTED.value: set(),
        BranchToAdminStatus.RECEIVED.value: set(),
    },
    RequestType.KITCHEN_TO_ADMIN: {
        # Only REJECT is a generic transition. PENDING -> ALLOCATED (Admin),
        # ALLOCATED -> DISPATCHED (kitchen) and DISPATCHED -> RECEIVED (branch,
        # once every allocation lands) each carry side effects — allocation
        # fan-out or a stock movement — so they run through dedicated endpoints
        # (/allocate, /dispatch, /deliveries/{id}/receive) and are deliberately
        # absent here. That is what stops a bare admin status PATCH from reaching
        # ALLOCATED and silently skipping the allocation write.
        KitchenToAdminStatus.PENDING.value: {
            KitchenToAdminStatus.REJECTED.value,
        },
        KitchenToAdminStatus.ALLOCATED.value: set(),
        KitchenToAdminStatus.DISPATCHED.value: set(),
        KitchenToAdminStatus.RECEIVED.value: set(),
        KitchenToAdminStatus.REJECTED.value: set(),
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

# BRANCH_TO_ADMIN for a kitchen-off tenant: there is no kitchen to forward to, so
# the kitchen production leg (FORWARDED_TO_KITCHEN -> IN_PRODUCTION -> PRODUCED) is
# removed. Admin approves and dispatches straight from a warehouse; the branch
# receives. Selected by request restaurant's has_central_kitchen (see
# validate_transition). REJECTED/RECEIVED stay terminal.
_BRANCH_TO_ADMIN_NO_KITCHEN_TRANSITIONS: dict[str, set[str]] = {
    BranchToAdminStatus.PENDING.value: {
        BranchToAdminStatus.APPROVED.value,
        BranchToAdminStatus.REJECTED.value,
        BranchToAdminStatus.PARTIALLY_APPROVED.value,
    },
    BranchToAdminStatus.APPROVED.value: {
        BranchToAdminStatus.DISPATCHED.value,
    },
    BranchToAdminStatus.PARTIALLY_APPROVED.value: {
        BranchToAdminStatus.DISPATCHED.value,
    },
    BranchToAdminStatus.DISPATCHED.value: {
        BranchToAdminStatus.RECEIVED.value,
    },
    BranchToAdminStatus.REJECTED.value: set(),
    BranchToAdminStatus.RECEIVED.value: set(),
}

# BRANCH_TO_ADMIN, kitchen tenant, request is resale-only (every line is a
# product the kitchen does NOT make — a bottled Coke, packaged goods). The
# kitchen holds these as stock and just ships them, so it may jump
# FORWARDED_TO_KITCHEN -> DISPATCHED, skipping the pointless production leg. The
# normal production path stays open too; only the direct edge is added. Mixed
# requests (any finished good) and any line whose kind can't be read never reach
# here — see _is_resale_only in requests.py — so they keep the full flow.
_BRANCH_TO_ADMIN_RESALE_ONLY_TRANSITIONS: dict[str, set[str]] = {
    **ALLOWED_TRANSITIONS[RequestType.BRANCH_TO_ADMIN],
    BranchToAdminStatus.FORWARDED_TO_KITCHEN.value: {
        BranchToAdminStatus.IN_PRODUCTION.value,
        BranchToAdminStatus.DISPATCHED.value,
    },
}


# Who may *create* each request type.
CREATE_ROLES: dict[RequestType, set[UserRole]] = {
    RequestType.KITCHEN_TO_WAREHOUSE: {UserRole.KITCHEN_MANAGER},
    RequestType.WAREHOUSE_TO_ADMIN_PO: {UserRole.WAREHOUSE_MANAGER},
    RequestType.BRANCH_TO_ADMIN: {UserRole.BRANCH_MANAGER},
    RequestType.ADMIN_TO_SUPERADMIN_PLAN: {UserRole.ADMIN},
    RequestType.KITCHEN_TO_ADMIN: {UserRole.KITCHEN_MANAGER},
}

# Who may transition *to* each target status.
TRANSITION_ROLES: dict[RequestType, dict[str, set[UserRole]]] = {
    RequestType.KITCHEN_TO_WAREHOUSE: {
        KitchenToWarehouseStatus.APPROVED.value: {UserRole.WAREHOUSE_MANAGER},
        KitchenToWarehouseStatus.PARTIALLY_APPROVED.value: {UserRole.WAREHOUSE_MANAGER},
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
        BranchToAdminStatus.DISPATCHED.value: {UserRole.KITCHEN_MANAGER},
        BranchToAdminStatus.RECEIVED.value: {UserRole.BRANCH_MANAGER},
    },
    RequestType.KITCHEN_TO_ADMIN: {
        # Only the generic REJECT is role-gated here; the allocate/dispatch/
        # receive transitions are authorised inside their dedicated endpoints.
        KitchenToAdminStatus.REJECTED.value: {UserRole.ADMIN},
    },
    RequestType.ADMIN_TO_SUPERADMIN_PLAN: {
        AdminToSuperAdminStatus.APPROVED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.PARTIALLY_APPROVED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.REJECTED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.APPLIED.value: {UserRole.SUPER_ADMIN},
        AdminToSuperAdminStatus.CONFIRMED.value: {UserRole.ADMIN},
    },
}

# Kitchen-off overrides: with no central kitchen, a branch's raw-material request
# is fulfilled directly by the WAREHOUSE, exactly like KITCHEN_TO_WAREHOUSE — the
# branch names a warehouse at create, the warehouse manager approves and
# dispatches from its stock, and the branch receives. Admin is out of this loop.
# Only the differing statuses are listed; RECEIVED falls back to
# TRANSITION_ROLES[BRANCH_TO_ADMIN] (BRANCH_MANAGER).
_BRANCH_TO_ADMIN_NO_KITCHEN_ROLES: dict[str, set[UserRole]] = {
    BranchToAdminStatus.APPROVED.value: {UserRole.WAREHOUSE_MANAGER},
    BranchToAdminStatus.PARTIALLY_APPROVED.value: {UserRole.WAREHOUSE_MANAGER},
    BranchToAdminStatus.REJECTED.value: {UserRole.WAREHOUSE_MANAGER},
    BranchToAdminStatus.DISPATCHED.value: {UserRole.WAREHOUSE_MANAGER},
}


PARTIAL_APPROVAL_TYPES: set[RequestType] = {
    RequestType.KITCHEN_TO_WAREHOUSE,
    RequestType.WAREHOUSE_TO_ADMIN_PO,
    RequestType.BRANCH_TO_ADMIN,
    RequestType.ADMIN_TO_SUPERADMIN_PLAN,
}

PARTIAL_APPROVAL_STATUSES: set[str] = {
    KitchenToWarehouseStatus.PARTIALLY_APPROVED.value,
    WarehouseToAdminStatus.PARTIALLY_APPROVED.value,
    BranchToAdminStatus.PARTIALLY_APPROVED.value,
    AdminToSuperAdminStatus.PARTIALLY_APPROVED.value,
}

FULL_APPROVAL_STATUSES: set[str] = {
    KitchenToWarehouseStatus.APPROVED.value,
    WarehouseToAdminStatus.APPROVED.value,
    BranchToAdminStatus.APPROVED.value,
    AdminToSuperAdminStatus.APPROVED.value,
}


def supports_partial_approval(request_type: RequestType) -> bool:
    return request_type in PARTIAL_APPROVAL_TYPES


def _transitions_for(
    request_type: RequestType,
    *,
    has_central_kitchen: bool,
    resale_only: bool = False,
) -> dict[str, set[str]]:
    if request_type == RequestType.BRANCH_TO_ADMIN:
        if not has_central_kitchen:
            return _BRANCH_TO_ADMIN_NO_KITCHEN_TRANSITIONS
        if resale_only:
            return _BRANCH_TO_ADMIN_RESALE_ONLY_TRANSITIONS
    return ALLOWED_TRANSITIONS.get(request_type, {})


def validate_transition(
    request_type: RequestType,
    from_status: str,
    to_status: str,
    *,
    has_central_kitchen: bool = True,
    resale_only: bool = False,
) -> None:
    type_map = _transitions_for(
        request_type,
        has_central_kitchen=has_central_kitchen,
        resale_only=resale_only,
    )
    allowed = type_map.get(from_status, set())
    if to_status not in allowed:
        raise ConflictError(
            f"Cannot transition from {from_status} to {to_status}.",
            code="invalid_transition",
        )


def roles_allowed_to_create(request_type: RequestType) -> set[UserRole]:
    return CREATE_ROLES.get(request_type, set())


def roles_allowed_to_transition(
    request_type: RequestType,
    to_status: str,
    *,
    has_central_kitchen: bool = True,
) -> set[UserRole]:
    if request_type == RequestType.BRANCH_TO_ADMIN and not has_central_kitchen:
        override = _BRANCH_TO_ADMIN_NO_KITCHEN_ROLES.get(to_status)
        if override is not None:
            return override
    return TRANSITION_ROLES.get(request_type, {}).get(to_status, set())


def assert_role_can_create(actor_role: UserRole, request_type: RequestType) -> None:
    if actor_role not in roles_allowed_to_create(request_type):
        raise ForbiddenError("You are not allowed to create this request type.")


def assert_role_can_transition(
    actor_role: UserRole,
    request_type: RequestType,
    to_status: str,
    *,
    has_central_kitchen: bool = True,
) -> None:
    allowed = roles_allowed_to_transition(
        request_type, to_status, has_central_kitchen=has_central_kitchen
    )
    if actor_role not in allowed:
        raise ForbiddenError("You are not allowed to perform this transition.")
