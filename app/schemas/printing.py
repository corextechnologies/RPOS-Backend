"""Schemas for POS printing config (stations, printers, routing maps).

Branch-manager facing — the device-facing read bundle (GET /pos/config) arrives
in a later phase. See app/api/v1/branch/printing.py.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.printing_enums import (
    PrinterConnection,
    PrinterProtocol,
    PrinterRole,
    PrinterStatus,
)


# ----- stations --------------------------------------------------------------

class StationIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=32)
    sort_order: int = 0
    #: The fallback/expeditor station. At most one per branch.
    is_expo: bool = False


class StationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    code: str | None = Field(default=None, min_length=1, max_length=32)
    sort_order: int | None = None
    is_expo: bool | None = None


class StationOut(BaseModel):
    id: int
    branch_id: int
    name: str
    code: str
    sort_order: int
    is_expo: bool

    model_config = {"from_attributes": True}


# ----- printers --------------------------------------------------------------

class PrinterIn(BaseModel):
    role: PrinterRole
    connection: PrinterConnection
    protocol: PrinterProtocol = PrinterProtocol.ESC_POS
    #: KITCHEN printers point at a station; RECEIPT printers may bind a device.
    station_id: int | None = None
    device_id: int | None = None
    name: str | None = Field(default=None, max_length=255)
    #: ip:port (LAN) | MAC (BT) | device path (USB).
    address: str | None = Field(default=None, max_length=255)


class PrinterUpdate(BaseModel):
    role: PrinterRole | None = None
    connection: PrinterConnection | None = None
    protocol: PrinterProtocol | None = None
    station_id: int | None = None
    device_id: int | None = None
    name: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)
    status: PrinterStatus | None = None


class PrinterOut(BaseModel):
    id: int
    branch_id: int
    role: PrinterRole
    connection: PrinterConnection
    protocol: PrinterProtocol
    status: PrinterStatus
    station_id: int | None = None
    device_id: int | None = None
    name: str | None = None
    address: str | None = None
    last_seen_at: datetime | None = None

    model_config = {"from_attributes": True}


# ----- routing maps ----------------------------------------------------------

class CategoryMapIn(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    station_id: int


class CategoryMapOut(BaseModel):
    id: int
    branch_id: int
    category: str
    station_id: int

    model_config = {"from_attributes": True}


class ItemStationIn(BaseModel):
    menu_item_id: int
    station_id: int


class ItemStationOut(BaseModel):
    id: int
    branch_id: int
    menu_item_id: int
    station_id: int

    model_config = {"from_attributes": True}


# ----- resolution (debug/read) ----------------------------------------------

class ResolvedStationOut(BaseModel):
    menu_item_id: int
    station: StationOut | None = None
