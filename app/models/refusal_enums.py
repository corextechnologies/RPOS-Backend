"""Why a sale could not be made, and how we came to hear about it."""
from __future__ import annotations

import enum


class RefusalReason(str, enum.Enum):
    """Why the till could not sell the item.

    The distinction is the whole point of recording a cause rather than just a
    count. OUT_OF_STOCK is unmet demand — a customer wanted it and we had run
    out. STAFF_PULLED is a decision: the item was taken off sale for a quality
    problem, a broken machine, whatever. The customer still went without, so it
    belongs in a report, but it is NOT a stock shortfall and must never inflate a
    forecast — that would tell the kitchen to make more of something we chose not
    to sell.

    OUT_OF_STOCK   — nothing left on the shelf.
    SHORT_STOCK    — some left, but fewer than asked for. Carries the gap.
    STAFF_PULLED   — manually taken off sale (an "86").
    """

    OUT_OF_STOCK = "OUT_OF_STOCK"
    SHORT_STOCK = "SHORT_STOCK"
    STAFF_PULLED = "STAFF_PULLED"


#: The reasons that represent genuine unmet demand, and so may feed forecasting.
#: STAFF_PULLED is deliberately absent — see the enum docstring.
DEMAND_REASONS: frozenset[RefusalReason] = frozenset(
    {RefusalReason.OUT_OF_STOCK, RefusalReason.SHORT_STOCK}
)


class RefusalSource(str, enum.Enum):
    """How the record reached the server.

    ONLINE — the till was connected; recorded as it happened.
    SYNC   — the till was offline and queued it, replayed on reconnect.

    Worth distinguishing because offline data is thinner: a device decides
    availability from its own cached stock, so its refusals are its best guess
    rather than the server's view.
    """

    ONLINE = "ONLINE"
    SYNC = "SYNC"
