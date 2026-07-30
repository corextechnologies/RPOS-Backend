"""Enums for POS kitchen/receipt printing, in their own module so models can
import them without a cycle (mirrors menu_enums / request_enums).

These describe how the *ordering app* reaches a physical printer. The backend
never renders ESC/POS and never opens a socket — it only stores this config and
hands it to the device, which is the print controller. See app/models/printing.py.
"""
import enum


class PrinterRole(str, enum.Enum):
    """What a printer is for.

    KITCHEN — a prep-station printer that receives the (unpriced) kitchen ticket.
    RECEIPT — the customer-receipt printer bound to a counter device (or, on a
              curbside tablet, its paired Bluetooth printer).
    """

    KITCHEN = "KITCHEN"
    RECEIPT = "RECEIPT"


class PrinterConnection(str, enum.Enum):
    """How the device reaches the printer.

    LAN — raw TCP to `address` ("192.168.1.50:9100"); the default, works during
          an internet outage because it never leaves the branch network.
    USB — a printer physically attached to the device.
    BT  — Bluetooth, typically a curbside tablet's paired receipt printer.
    """

    LAN = "LAN"
    USB = "USB"
    BT = "BT"


class PrinterProtocol(str, enum.Enum):
    """The command language the device renders to. (Exposed to the device as the
    printer's `protocol`; ESC/POS covers Epson TM-series, STAR covers Star TSP.)"""

    ESC_POS = "ESC_POS"
    STAR = "STAR"


class PrinterStatus(str, enum.Enum):
    """Last-known reachability, reported by the device (print-result ACKs land in
    a later phase). A freshly registered printer is UNKNOWN until first contact."""

    UNKNOWN = "UNKNOWN"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"


class PrintKind(str, enum.Enum):
    """What a print job prints: an (unpriced) kitchen station ticket, or the
    (priced) customer receipt. One KITCHEN job per resolved station, one RECEIPT
    job per order."""

    KITCHEN = "KITCHEN"
    RECEIPT = "RECEIPT"


class PrintJobState(str, enum.Enum):
    """Lifecycle of one ticket. The device renders + pushes, then ACKs the
    outcome (Phase 4). QUEUED is emitted at send/sync; PRINTED is terminal;
    FAILED is retryable; VOID is set when a since-modified order is voided
    (Phase 7)."""

    QUEUED = "QUEUED"
    PRINTED = "PRINTED"
    FAILED = "FAILED"
    VOID = "VOID"
