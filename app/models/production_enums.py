"""Enums for the branch sub-kitchen production log.

Kept in their own module so app/models/production.py can be imported without a
cycle, matching the request_enums.py precedent.
"""
import enum


class ProductionLineRole(str, enum.Enum):
    """Which side of a production run a line sits on.

    INPUT is consumed from branch stock, OUTPUT is produced into it.
    """

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
