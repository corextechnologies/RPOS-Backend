"""Request workflow enums — single source of truth for all four portal flows."""
from __future__ import annotations

import enum


class RequestType(str, enum.Enum):
    KITCHEN_TO_WAREHOUSE = "KITCHEN_TO_WAREHOUSE"
    WAREHOUSE_TO_ADMIN_PO = "WAREHOUSE_TO_ADMIN_PO"
    BRANCH_TO_ADMIN = "BRANCH_TO_ADMIN"
    ADMIN_TO_SUPERADMIN_PLAN = "ADMIN_TO_SUPERADMIN_PLAN"
    # A kitchen tells Admin a production batch is ready; Admin allocates it across
    # branches (one request fans out to many), each branch receiving its slice
    # independently. Unlike every other flow this is one-source-to-many-targets,
    # so its fan-out lives in request_allocations, not on the single target_* pair.
    KITCHEN_TO_ADMIN = "KITCHEN_TO_ADMIN"


class LocationType(str, enum.Enum):
    BRANCH = "BRANCH"
    KITCHEN = "KITCHEN"
    WAREHOUSE = "WAREHOUSE"


# Status values stored as strings on Request.status (per-type subsets).
class KitchenToWarehouseStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    DISPATCHED = "DISPATCHED"
    RECEIVED = "RECEIVED"


class WarehouseToAdminStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    # Admin has sent the goods. (Was IN_QUEUE.) Note KitchenToWarehouseStatus
    # also has a DISPATCHED — the state machine keys on RequestType first, so
    # the two never collide, but don't assume a bare "DISPATCHED" is this flow.
    DISPATCHED = "DISPATCHED"
    # Warehouse took delivery but something was short or damaged.
    REPORTED = "REPORTED"
    # Admin reviewed the report and accepted it — warehouse may now receive.
    RESOLVED = "RESOLVED"
    RECEIVED = "RECEIVED"


class BranchToAdminStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    FORWARDED_TO_KITCHEN = "FORWARDED_TO_KITCHEN"
    IN_PRODUCTION = "IN_PRODUCTION"
    PRODUCED = "PRODUCED"
    ALLOCATED = "ALLOCATED"
    RECEIVED = "RECEIVED"


class KitchenToAdminStatus(str, enum.Enum):
    PENDING = "PENDING"
    # Admin has split the ready batch across branches (allocations written). The
    # move PENDING -> ALLOCATED happens only via the /allocate endpoint, never a
    # bare status PATCH, so the admin inbox can only ever REJECT from PENDING.
    ALLOCATED = "ALLOCATED"
    # The kitchen has shipped every allocation; kitchen finished-goods debited.
    DISPATCHED = "DISPATCHED"
    # Every allocation has been received by its branch (per-allocation receipts
    # roll up to here once the last one lands).
    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"


class AllocationStatus(str, enum.Enum):
    """Per-branch lifecycle of one slice of a KITCHEN_TO_ADMIN batch.

    Tracked on request_allocations so branches receive independently: the request
    is DISPATCHED as a whole (one kitchen shipment), but each branch confirms its
    own slice, and the request only reaches RECEIVED once all of them have.
    """

    ALLOCATED = "ALLOCATED"
    DISPATCHED = "DISPATCHED"
    RECEIVED = "RECEIVED"


class AdminToSuperAdminStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    CONFIRMED = "CONFIRMED"


INITIAL_STATUS: dict[RequestType, str] = {
    RequestType.KITCHEN_TO_WAREHOUSE: KitchenToWarehouseStatus.PENDING.value,
    RequestType.WAREHOUSE_TO_ADMIN_PO: WarehouseToAdminStatus.PENDING.value,
    RequestType.BRANCH_TO_ADMIN: BranchToAdminStatus.PENDING.value,
    RequestType.ADMIN_TO_SUPERADMIN_PLAN: AdminToSuperAdminStatus.PENDING.value,
    RequestType.KITCHEN_TO_ADMIN: KitchenToAdminStatus.PENDING.value,
}
