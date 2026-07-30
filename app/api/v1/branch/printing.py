"""Branch printing config: stations, printers, and item→station routing.

A Branch Manager owns the print topology of their branch — which prep stations
exist, which printer each ticket goes to, and how a menu line routes to a
station. This is POS-printing config only; it is unrelated to the Kitchen
commissary or production. The device reads a cached bundle of this (GET
/pos/config) in a later phase; here it is authored.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.deps.rbac import require_actor_branch_id
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.printing import (
    CategoryMapIn,
    CategoryMapOut,
    ItemStationIn,
    ItemStationOut,
    PrinterIn,
    PrinterOut,
    PrinterUpdate,
    ResolvedStationOut,
    StationIn,
    StationOut,
    StationUpdate,
)
from app.services.printing import PrintingConfigService, RoutingService

router = APIRouter(
    prefix="/printing",
    dependencies=[Depends(require_role(UserRole.BRANCH_MANAGER))],
)


def _dump(schema, row) -> dict:
    return schema.model_validate(row).model_dump(mode="json")


# ----- stations --------------------------------------------------------------

@router.post("/stations")
def create_station(
    body: StationIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    station = PrintingConfigService.create_station(db, current, branch_id, body)
    return ok(_dump(StationOut, station))


@router.get("/stations")
def list_stations(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = PrintingConfigService.list_stations(db, current, branch_id)
    return ok([_dump(StationOut, r) for r in rows])


@router.patch("/stations/{station_id}")
def update_station(
    station_id: int,
    body: StationUpdate,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    station = PrintingConfigService.update_station(db, current, branch_id, station_id, body)
    return ok(_dump(StationOut, station))


@router.delete("/stations/{station_id}")
def delete_station(
    station_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    PrintingConfigService.delete_station(db, current, branch_id, station_id)
    return ok({"id": station_id, "deleted": True})


# ----- printers --------------------------------------------------------------

@router.post("/printers")
def create_printer(
    body: PrinterIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    printer = PrintingConfigService.create_printer(db, current, branch_id, body)
    return ok(_dump(PrinterOut, printer))


@router.get("/printers")
def list_printers(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = PrintingConfigService.list_printers(db, current, branch_id)
    return ok([_dump(PrinterOut, r) for r in rows])


@router.patch("/printers/{printer_id}")
def update_printer(
    printer_id: int,
    body: PrinterUpdate,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    printer = PrintingConfigService.update_printer(db, current, branch_id, printer_id, body)
    return ok(_dump(PrinterOut, printer))


@router.delete("/printers/{printer_id}")
def delete_printer(
    printer_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    PrintingConfigService.delete_printer(db, current, branch_id, printer_id)
    return ok({"id": printer_id, "deleted": True})


# ----- category map ----------------------------------------------------------

@router.put("/category-map")
def upsert_category_map(
    body: CategoryMapIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    row = PrintingConfigService.upsert_category_map(db, current, branch_id, body)
    return ok(_dump(CategoryMapOut, row))


@router.get("/category-map")
def list_category_maps(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = PrintingConfigService.list_category_maps(db, current, branch_id)
    return ok([_dump(CategoryMapOut, r) for r in rows])


@router.delete("/category-map/{map_id}")
def delete_category_map(
    map_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    PrintingConfigService.delete_category_map(db, current, branch_id, map_id)
    return ok({"id": map_id, "deleted": True})


# ----- item override ---------------------------------------------------------

@router.put("/item-stations")
def upsert_item_station(
    body: ItemStationIn,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    row = PrintingConfigService.upsert_item_station(db, current, branch_id, body)
    return ok(_dump(ItemStationOut, row))


@router.get("/item-stations")
def list_item_stations(
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    rows = PrintingConfigService.list_item_stations(db, current, branch_id)
    return ok([_dump(ItemStationOut, r) for r in rows])


@router.delete("/item-stations/{override_id}")
def delete_item_station(
    override_id: int,
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    branch_id = require_actor_branch_id(current)
    PrintingConfigService.delete_item_station(db, current, branch_id, override_id)
    return ok({"id": override_id, "deleted": True})


# ----- resolution (debug/read) ----------------------------------------------

@router.get("/resolve")
def resolve_station(
    menu_item_id: int = Query(..., ge=1),
    current: User = Depends(require_role(UserRole.BRANCH_MANAGER)),
    db: Session = Depends(get_db),
):
    """Which station would this item's ticket print at? item override → category
    map → expo. `station` is null only when the branch has no expo configured."""
    branch_id = require_actor_branch_id(current)
    station = RoutingService.resolve(db, branch_id, menu_item_id)
    payload = ResolvedStationOut(
        menu_item_id=menu_item_id,
        station=StationOut.model_validate(station) if station is not None else None,
    )
    return ok(payload.model_dump(mode="json"))
