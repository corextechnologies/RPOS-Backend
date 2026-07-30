"""Sub-kitchen (branch prep board) enums."""
from __future__ import annotations

import enum


class PrepSource(str, enum.Enum):
    """Why a prep ticket exists.

    BATCH — the sub-chef proactively prepares stock ahead of a rush (slice onions,
            whip cream, bake off bases). Created by hand from the prep board.
    ORDER — a customer ordered a made-to-order item (a named cake), so the order
            auto-created a finishing job. Carries the customer's note. Populated
            by the order-linkage slice; the columns exist from the start.
    """

    BATCH = "BATCH"
    ORDER = "ORDER"


class PrepStatus(str, enum.Enum):
    """A prep ticket's lifecycle on the board.

    QUEUED -> IN_PROGRESS -> READY -> COMPLETED, or CANCELLED from any non-terminal
    state. Only COMPLETED moves stock (it consumes components and produces the
    finished good), which is why it runs through a dedicated endpoint, not a bare
    status change — the same discipline the request state machine uses.
    """

    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    READY = "READY"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


#: Board states a ticket may still be worked from (not yet finished or cancelled).
OPEN_STATUSES: frozenset[PrepStatus] = frozenset(
    {PrepStatus.QUEUED, PrepStatus.IN_PROGRESS, PrepStatus.READY}
)

#: Plain status moves allowed via the status endpoint. COMPLETED is deliberately
#: absent — it has stock side effects and goes through /complete instead.
STATUS_TRANSITIONS: dict[PrepStatus, set[PrepStatus]] = {
    PrepStatus.QUEUED: {PrepStatus.IN_PROGRESS, PrepStatus.CANCELLED},
    PrepStatus.IN_PROGRESS: {PrepStatus.READY, PrepStatus.CANCELLED},
    PrepStatus.READY: {PrepStatus.IN_PROGRESS, PrepStatus.CANCELLED},
    PrepStatus.COMPLETED: set(),
    PrepStatus.CANCELLED: set(),
}
