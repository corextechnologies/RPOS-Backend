"""Request workflow enums — single source of truth for all four portal flows."""
from __future__ import annotations

import enum


class RequestType(str, enum.Enum):
    KITCHEN_TO_WAREHOUSE = "KITCHEN_TO_WAREHOUSE"
    WAREHOUSE_TO_ADMIN_PO = "WAREHOUSE_TO_ADMIN_PO"
    BRANCH_TO_ADMIN = "BRANCH_TO_ADMIN"
    ADMIN_TO_SUPERADMIN_PLAN = "ADMIN_TO_SUPERADMIN_PLAN"


class LocationType(str, enum.Enum):
    BRANCH = "BRANCH"
    KITCHEN = "KITCHEN"
    WAREHOUSE = "WAREHOUSE"


# Status values stored as strings on Request.status (per-type subsets).
class KitchenToWarehouseStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DISPATCHED = "DISPATCHED"
    RECEIVED = "RECEIVED"


class WarehouseToAdminStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    IN_QUEUE = "IN_QUEUE"
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
}
