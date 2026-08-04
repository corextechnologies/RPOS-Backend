"""Menu + order enums, in their own module so models can import without a cycle."""
import enum


class MenuVersionStatus(str, enum.Enum):
    """A menu version is a publish artefact, not a mutable record.

    DRAFT can be edited; PUBLISHED never changes again — an order pins the
    version that priced it, so editing a published menu would silently re-price
    history. Superseding publishes a new version and ARCHIVEs the old one.
    """

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class MenuProposalStatus(str, enum.Enum):
    """A branch's proposed menu item, awaiting Admin review.

    A branch manager can add a dish to the menu, but only Admin makes the menu
    live. A proposal is the hand-off: the branch submits the dish, Admin sets the
    final price and either APPROVES it (it becomes ready to publish onto the live
    menu) or REJECTS it with a reason. PENDING is the only editable/withdrawable
    state; APPROVED/REJECTED are terminal.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OrderStatus(str, enum.Enum):
    DRAFT = "DRAFT"          # being built on the device
    PARKED = "PARKED"        # held; the customer stepped away
    SENT = "SENT"            # fired to the kitchen; stock deducted
    PREPARING = "PREPARING"
    READY = "READY"
    SERVED = "SERVED"
    VOID = "VOID"


class VoidState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    VOIDED = "VOIDED"


class FlaggedReason(str, enum.Enum):
    """Why an order is flagged for manager review on offline sync (POS-4).

    A flagged order is never rejected — the sale physically happened — only
    surfaced. PRICE_DRIFT: the device priced it differently than the server.
    STOCK_OVERSELL: it sold past on-hand while offline, so stock could not be
    fully deducted (the manager reconciles the shortfall)."""

    PRICE_DRIFT = "PRICE_DRIFT"
    STOCK_OVERSELL = "STOCK_OVERSELL"
