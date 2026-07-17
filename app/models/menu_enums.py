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
