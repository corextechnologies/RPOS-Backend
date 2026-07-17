"""Capability-based authorization for branch / POS actors.

`position` (SALESPERSON / CASHIER / ORDER_TAKER) is an org label; a *capability*
is the authorization fact. Gate endpoints on capability, not on position, so the
rule survives the POS phases — where a curbside ORDER_TAKER on a drawer-less
tablet must not be able to take cash even though a CASHIER can.

Only a handful of capabilities are actually enforced in Phase 5.1 (ORDER_*,
CUSTOMER_*, INVENTORY_READ). The rest are declared now so later POS slices add
rows to `_POSITION_CAPS` instead of inventing a second scheme. The eventual POS
rule is `capabilities_for(user) & capabilities_of(device)`; this module owns the
user half.
"""
from __future__ import annotations

import enum

from fastapi import Depends

from app.core.exceptions import ForbiddenError
from app.deps.auth import get_current_user
from app.models.enums import BranchPosition, UserRole
from app.models.user import User


class Capability(str, enum.Enum):
    """A single permitted action at a branch. Enforced where noted; the rest are
    reserved for the POS slices that introduce shifts, drawers and payments."""

    # Enforced in Phase 5.1.
    ORDER_READ = "ORDER_READ"
    ORDER_CREATE = "ORDER_CREATE"
    CUSTOMER_READ = "CUSTOMER_READ"
    CUSTOMER_CREATE = "CUSTOMER_CREATE"
    INVENTORY_READ = "INVENTORY_READ"
    # Reserved for POS-2 / POS-3.
    PAYMENT_TAKE = "PAYMENT_TAKE"      # card / wallet
    PAYMENT_CASH = "PAYMENT_CASH"      # tender + change (needs a drawer)
    DRAWER_OPEN = "DRAWER_OPEN"
    SHIFT_OPEN = "SHIFT_OPEN"
    SHIFT_CLOSE = "SHIFT_CLOSE"
    # Manager-held.
    WASTE_LOG = "WASTE_LOG"
    BRANCH_REQUEST_CREATE = "BRANCH_REQUEST_CREATE"
    VOID_AFTER_SEND = "VOID_AFTER_SEND"
    DISCOUNT_OVER_LIMIT = "DISCOUNT_OVER_LIMIT"
    REFUND = "REFUND"
    PRICE_OVERRIDE = "PRICE_OVERRIDE"


# The sell floor every branch sub-staff position shares.
_SELL_FLOOR = frozenset(
    {
        Capability.ORDER_READ,
        Capability.ORDER_CREATE,
        Capability.CUSTOMER_READ,
        Capability.CUSTOMER_CREATE,
        Capability.INVENTORY_READ,
    }
)

_POSITION_CAPS: dict[BranchPosition, frozenset[Capability]] = {
    # The curbside tablet: takes the order, sends the car to the counter to pay.
    BranchPosition.ORDER_TAKER: _SELL_FLOOR,
    # Sells and settles non-cash; owns no drawer or shift.
    BranchPosition.SALESPERSON: _SELL_FLOOR | frozenset({Capability.PAYMENT_TAKE}),
    # The counter terminal: full tender + drawer + shift lifecycle.
    BranchPosition.CASHIER: _SELL_FLOOR
    | frozenset(
        {
            Capability.PAYMENT_TAKE,
            Capability.PAYMENT_CASH,
            Capability.DRAWER_OPEN,
            Capability.SHIFT_OPEN,
            Capability.SHIFT_CLOSE,
        }
    ),
}

# The branch manager holds every branch capability, including the approval-gated
# ones (void-after-send, over-limit discount, refund, price override).
_ALL_BRANCH_CAPS = frozenset(Capability)


def capabilities_for(user: User) -> frozenset[Capability]:
    """The set of capabilities this user holds by role/position."""
    if user.role == UserRole.BRANCH_MANAGER:
        return _ALL_BRANCH_CAPS
    if user.role == UserRole.BRANCH_STAFF:
        if user.position is None:
            # Fail closed: a BRANCH_STAFF row with no position was written outside
            # the provisioning API. Never default it to a privilege set.
            return frozenset()
        return _POSITION_CAPS.get(user.position, frozenset())
    return frozenset()


def has_capability(user: User, cap: Capability) -> bool:
    return cap in capabilities_for(user)


def require_capability(*caps: Capability):
    """Dependency factory: require the caller to hold ALL listed capabilities.

    Mirrors `app.deps.auth.require_role`. Pair it with a router-level
    `require_role(BRANCH_MANAGER, BRANCH_STAFF)` so a non-branch role gets the
    generic 403 first and this only bites branch staff whose position is too low.
    """
    required = frozenset(caps)

    def _guard(current: User = Depends(get_current_user)) -> User:
        if not required.issubset(capabilities_for(current)):
            raise ForbiddenError(
                "Your position does not permit this action.",
                code="position_forbidden",
            )
        return current

    return _guard
