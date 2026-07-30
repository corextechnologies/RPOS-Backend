"""POS printing config: routing resolution + branch-manager CRUD.

Two things live here:

* `RoutingService.resolve` — the pure "which station does this line print at?"
  decision, reused online (in send) and mirrored by the device offline. It never
  raises and never silently drops: item override → category map → expo fallback.
* `PrintingConfigService` — the branch manager's CRUD over stations, printers and
  the two routing maps. Every mutation validates branch ownership, so a manager
  can never wire a printer to another branch's station, and is audited like the
  rest of the POS surface (see DeviceService in app/services/pos.py).

These are POS-printing entities only; nothing here touches the `Kitchen`
commissary model or `ProductionRun` (see app/models/printing.py).
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.menu import MenuItem, MenuVersion
from app.models.printing import (
    MenuItemStation,
    Printer,
    Station,
    StationCategoryMap,
)
from app.models.printing_enums import PrinterRole
from app.models.user import User
from app.services.audit import AuditService


class RoutingService:
    """Resolve a menu line to the station whose printer should get its ticket."""

    @staticmethod
    def resolve(db: Session, branch_id: int, menu_item_id: int) -> Station | None:
        """item override → category map → branch expo. Returns None only when the
        branch has no expo configured at all — a setup gap for the manager to fix,
        surfaced (never a silent drop) by the caller in a later phase."""
        override = db.execute(
            select(MenuItemStation).where(
                MenuItemStation.branch_id == branch_id,
                MenuItemStation.menu_item_id == menu_item_id,
            )
        ).scalar_one_or_none()
        if override is not None:
            return db.get(Station, override.station_id)

        item = db.get(MenuItem, menu_item_id)
        if item is not None and item.category:
            mapping = db.execute(
                select(StationCategoryMap).where(
                    StationCategoryMap.branch_id == branch_id,
                    StationCategoryMap.category == item.category,
                )
            ).scalar_one_or_none()
            if mapping is not None:
                return db.get(Station, mapping.station_id)

        return RoutingService.expo_station(db, branch_id)

    @staticmethod
    def expo_station(db: Session, branch_id: int) -> Station | None:
        """The branch's fallback/expeditor station, if one is configured."""
        return db.execute(
            select(Station)
            .where(Station.branch_id == branch_id, Station.is_expo.is_(True))
            .order_by(Station.sort_order, Station.id)
        ).scalars().first()


class PrintingConfigService:
    """Branch-manager CRUD over the routing/printer config for one branch."""

    # ----- ownership helpers -------------------------------------------------

    @staticmethod
    def _own_station(db: Session, manager: User, branch_id: int, station_id: int) -> Station:
        station = db.get(Station, station_id)
        if (
            station is None
            or station.restaurant_id != manager.restaurant_id
            or station.branch_id != branch_id
        ):
            raise NotFoundError("Station not found.", code="station_not_found")
        return station

    @staticmethod
    def _own_printer(db: Session, manager: User, branch_id: int, printer_id: int) -> Printer:
        printer = db.get(Printer, printer_id)
        if (
            printer is None
            or printer.restaurant_id != manager.restaurant_id
            or printer.branch_id != branch_id
        ):
            raise NotFoundError("Printer not found.", code="printer_not_found")
        return printer

    @staticmethod
    def _assert_menu_item_in_restaurant(db: Session, manager: User, menu_item_id: int) -> None:
        row = db.execute(
            select(MenuItem.id)
            .join(MenuVersion, MenuItem.menu_version_id == MenuVersion.id)
            .where(
                MenuItem.id == menu_item_id,
                MenuVersion.restaurant_id == manager.restaurant_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Menu item not found.", code="menu_item_not_found")

    @staticmethod
    def _assert_no_other_expo(db: Session, branch_id: int, exclude_id: int | None = None) -> None:
        q = select(Station.id).where(
            Station.branch_id == branch_id, Station.is_expo.is_(True)
        )
        if exclude_id is not None:
            q = q.where(Station.id != exclude_id)
        if db.execute(q).first() is not None:
            raise ConflictError(
                "This branch already has an expo station.", code="expo_exists"
            )

    # ----- stations ----------------------------------------------------------

    @staticmethod
    def create_station(db: Session, manager: User, branch_id: int, body) -> Station:
        clash = db.execute(
            select(Station).where(
                Station.branch_id == branch_id, Station.code == body.code
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(
                "A station with this code already exists.", code="station_code_exists"
            )
        if body.is_expo:
            PrintingConfigService._assert_no_other_expo(db, branch_id)

        station = Station(
            restaurant_id=manager.restaurant_id,
            branch_id=branch_id,
            name=body.name,
            code=body.code,
            sort_order=body.sort_order,
            is_expo=body.is_expo,
        )
        db.add(station)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.station.create",
            entity_type="pos_station",
            entity_id=station.id,
            restaurant_id=manager.restaurant_id,
            after={"code": station.code, "is_expo": station.is_expo, "branch_id": branch_id},
        )
        db.commit()
        db.refresh(station)
        return station

    @staticmethod
    def list_stations(db: Session, manager: User, branch_id: int) -> list[Station]:
        return list(
            db.execute(
                select(Station)
                .where(
                    Station.restaurant_id == manager.restaurant_id,
                    Station.branch_id == branch_id,
                )
                .order_by(Station.sort_order, Station.id)
            ).scalars()
        )

    @staticmethod
    def update_station(db: Session, manager: User, branch_id: int, station_id: int, body) -> Station:
        station = PrintingConfigService._own_station(db, manager, branch_id, station_id)
        fields = body.model_dump(exclude_unset=True)

        if "code" in fields and fields["code"] != station.code:
            clash = db.execute(
                select(Station).where(
                    Station.branch_id == branch_id,
                    Station.code == fields["code"],
                    Station.id != station.id,
                )
            ).scalar_one_or_none()
            if clash is not None:
                raise ConflictError(
                    "A station with this code already exists.",
                    code="station_code_exists",
                )
        if fields.get("is_expo") is True and not station.is_expo:
            PrintingConfigService._assert_no_other_expo(db, branch_id, exclude_id=station.id)

        for key, value in fields.items():
            setattr(station, key, value)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.station.update",
            entity_type="pos_station",
            entity_id=station.id,
            restaurant_id=manager.restaurant_id,
            after=fields,
        )
        db.commit()
        db.refresh(station)
        return station

    @staticmethod
    def delete_station(db: Session, manager: User, branch_id: int, station_id: int) -> None:
        station = PrintingConfigService._own_station(db, manager, branch_id, station_id)
        db.delete(station)
        AuditService.record(
            db,
            actor=manager,
            action="pos.station.delete",
            entity_type="pos_station",
            entity_id=station_id,
            restaurant_id=manager.restaurant_id,
            before={"code": station.code, "is_expo": station.is_expo},
        )
        db.commit()

    # ----- printers ----------------------------------------------------------

    @staticmethod
    def _validate_printer_targets(db: Session, manager: User, branch_id: int, body) -> None:
        """A printer may only point at this branch's own station/device."""
        station_id = getattr(body, "station_id", None)
        if station_id is not None:
            PrintingConfigService._own_station(db, manager, branch_id, station_id)
        device_id = getattr(body, "device_id", None)
        if device_id is not None:
            from app.models.pos import Device  # local import: avoid a cycle at module load

            device = db.get(Device, device_id)
            if (
                device is None
                or device.restaurant_id != manager.restaurant_id
                or device.branch_id != branch_id
            ):
                raise NotFoundError("Device not found.", code="device_not_found")

    @staticmethod
    def create_printer(db: Session, manager: User, branch_id: int, body) -> Printer:
        PrintingConfigService._validate_printer_targets(db, manager, branch_id, body)
        printer = Printer(
            restaurant_id=manager.restaurant_id,
            branch_id=branch_id,
            role=body.role,
            station_id=body.station_id,
            device_id=body.device_id,
            name=body.name,
            connection=body.connection,
            address=body.address,
            protocol=body.protocol,
        )
        db.add(printer)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.printer.create",
            entity_type="pos_printer",
            entity_id=printer.id,
            restaurant_id=manager.restaurant_id,
            after={"role": printer.role.value, "connection": printer.connection.value,
                   "address": printer.address, "branch_id": branch_id},
        )
        db.commit()
        db.refresh(printer)
        return printer

    @staticmethod
    def list_printers(db: Session, manager: User, branch_id: int) -> list[Printer]:
        return list(
            db.execute(
                select(Printer)
                .where(
                    Printer.restaurant_id == manager.restaurant_id,
                    Printer.branch_id == branch_id,
                )
                .order_by(Printer.id)
            ).scalars()
        )

    @staticmethod
    def update_printer(db: Session, manager: User, branch_id: int, printer_id: int, body) -> Printer:
        printer = PrintingConfigService._own_printer(db, manager, branch_id, printer_id)
        fields = body.model_dump(exclude_unset=True)
        # Re-validate targets against whatever the update would set them to.
        if "station_id" in fields and fields["station_id"] is not None:
            PrintingConfigService._own_station(db, manager, branch_id, fields["station_id"])
        if "device_id" in fields and fields["device_id"] is not None:
            from app.models.pos import Device

            device = db.get(Device, fields["device_id"])
            if (
                device is None
                or device.restaurant_id != manager.restaurant_id
                or device.branch_id != branch_id
            ):
                raise NotFoundError("Device not found.", code="device_not_found")

        for key, value in fields.items():
            setattr(printer, key, value)
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.printer.update",
            entity_type="pos_printer",
            entity_id=printer.id,
            restaurant_id=manager.restaurant_id,
            after=fields,
        )
        db.commit()
        db.refresh(printer)
        return printer

    @staticmethod
    def delete_printer(db: Session, manager: User, branch_id: int, printer_id: int) -> None:
        printer = PrintingConfigService._own_printer(db, manager, branch_id, printer_id)
        db.delete(printer)
        AuditService.record(
            db,
            actor=manager,
            action="pos.printer.delete",
            entity_type="pos_printer",
            entity_id=printer_id,
            restaurant_id=manager.restaurant_id,
            before={"role": printer.role.value, "address": printer.address},
        )
        db.commit()

    # ----- category map (upsert by (branch, category)) -----------------------

    @staticmethod
    def upsert_category_map(db: Session, manager: User, branch_id: int, body) -> StationCategoryMap:
        PrintingConfigService._own_station(db, manager, branch_id, body.station_id)
        row = db.execute(
            select(StationCategoryMap).where(
                StationCategoryMap.branch_id == branch_id,
                StationCategoryMap.category == body.category,
            )
        ).scalar_one_or_none()
        if row is None:
            row = StationCategoryMap(
                restaurant_id=manager.restaurant_id,
                branch_id=branch_id,
                category=body.category,
                station_id=body.station_id,
            )
            db.add(row)
        else:
            row.station_id = body.station_id
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.station_category_map.upsert",
            entity_type="pos_station_category_map",
            entity_id=row.id,
            restaurant_id=manager.restaurant_id,
            after={"category": row.category, "station_id": row.station_id},
        )
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def list_category_maps(db: Session, manager: User, branch_id: int) -> list[StationCategoryMap]:
        return list(
            db.execute(
                select(StationCategoryMap)
                .where(
                    StationCategoryMap.restaurant_id == manager.restaurant_id,
                    StationCategoryMap.branch_id == branch_id,
                )
                .order_by(StationCategoryMap.category)
            ).scalars()
        )

    @staticmethod
    def delete_category_map(db: Session, manager: User, branch_id: int, map_id: int) -> None:
        row = db.get(StationCategoryMap, map_id)
        if row is None or row.restaurant_id != manager.restaurant_id or row.branch_id != branch_id:
            raise NotFoundError("Mapping not found.", code="category_map_not_found")
        db.delete(row)
        AuditService.record(
            db,
            actor=manager,
            action="pos.station_category_map.delete",
            entity_type="pos_station_category_map",
            entity_id=map_id,
            restaurant_id=manager.restaurant_id,
            before={"category": row.category, "station_id": row.station_id},
        )
        db.commit()

    # ----- item override (upsert by (branch, menu_item)) ---------------------

    @staticmethod
    def upsert_item_station(db: Session, manager: User, branch_id: int, body) -> MenuItemStation:
        PrintingConfigService._assert_menu_item_in_restaurant(db, manager, body.menu_item_id)
        PrintingConfigService._own_station(db, manager, branch_id, body.station_id)
        row = db.execute(
            select(MenuItemStation).where(
                MenuItemStation.branch_id == branch_id,
                MenuItemStation.menu_item_id == body.menu_item_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = MenuItemStation(
                restaurant_id=manager.restaurant_id,
                branch_id=branch_id,
                menu_item_id=body.menu_item_id,
                station_id=body.station_id,
            )
            db.add(row)
        else:
            row.station_id = body.station_id
        db.flush()
        AuditService.record(
            db,
            actor=manager,
            action="pos.menu_item_station.upsert",
            entity_type="pos_menu_item_station",
            entity_id=row.id,
            restaurant_id=manager.restaurant_id,
            after={"menu_item_id": row.menu_item_id, "station_id": row.station_id},
        )
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def list_item_stations(db: Session, manager: User, branch_id: int) -> list[MenuItemStation]:
        return list(
            db.execute(
                select(MenuItemStation)
                .where(
                    MenuItemStation.restaurant_id == manager.restaurant_id,
                    MenuItemStation.branch_id == branch_id,
                )
                .order_by(MenuItemStation.menu_item_id)
            ).scalars()
        )

    @staticmethod
    def delete_item_station(db: Session, manager: User, branch_id: int, override_id: int) -> None:
        row = db.get(MenuItemStation, override_id)
        if row is None or row.restaurant_id != manager.restaurant_id or row.branch_id != branch_id:
            raise NotFoundError("Override not found.", code="item_station_not_found")
        db.delete(row)
        AuditService.record(
            db,
            actor=manager,
            action="pos.menu_item_station.delete",
            entity_type="pos_menu_item_station",
            entity_id=override_id,
            restaurant_id=manager.restaurant_id,
            before={"menu_item_id": row.menu_item_id, "station_id": row.station_id},
        )
        db.commit()

    # ----- device-facing config snapshot (Phase 1) ---------------------------

    @staticmethod
    def config_snapshot(
        db: Session, *, restaurant_id: int, branch_id: int, device_id: int
    ) -> tuple[dict, str]:
        """The whole print/routing config the device caches to route and print
        offline, plus a version fingerprint for ETag/If-None-Match.

        The version is a hash of the *routing config only* — printer runtime
        fields (`status`, `last_seen_at`) are deliberately excluded so a printer
        going online/offline does not force every device to re-pull its routing.
        The version therefore changes iff a station/printer/mapping is created,
        edited (incl. an address change) or deleted.
        """
        stations = list(
            db.execute(
                select(Station)
                .where(Station.restaurant_id == restaurant_id, Station.branch_id == branch_id)
                .order_by(Station.sort_order, Station.id)
            ).scalars()
        )
        printers = list(
            db.execute(
                select(Printer)
                .where(Printer.restaurant_id == restaurant_id, Printer.branch_id == branch_id)
                .order_by(Printer.id)
            ).scalars()
        )
        category_maps = list(
            db.execute(
                select(StationCategoryMap)
                .where(
                    StationCategoryMap.restaurant_id == restaurant_id,
                    StationCategoryMap.branch_id == branch_id,
                )
                .order_by(StationCategoryMap.category)
            ).scalars()
        )
        item_overrides = list(
            db.execute(
                select(MenuItemStation)
                .where(
                    MenuItemStation.restaurant_id == restaurant_id,
                    MenuItemStation.branch_id == branch_id,
                )
                .order_by(MenuItemStation.menu_item_id)
            ).scalars()
        )

        station_out = [
            {
                "id": s.id,
                "code": s.code,
                "name": s.name,
                "sort_order": s.sort_order,
                "is_expo": s.is_expo,
            }
            for s in stations
        ]
        printer_out = [
            {
                "id": p.id,
                "role": p.role.value,
                "station_id": p.station_id,
                "device_id": p.device_id,
                "connection": p.connection.value,
                "address": p.address,
                "protocol": p.protocol.value,
                "name": p.name,
                "status": p.status.value,
                "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
            }
            for p in printers
        ]
        category_out = [
            {"id": c.id, "category": c.category, "station_id": c.station_id}
            for c in category_maps
        ]
        item_out = [
            {"id": i.id, "menu_item_id": i.menu_item_id, "station_id": i.station_id}
            for i in item_overrides
        ]

        # This device's receipt printer, if one is bound to it.
        receipt = next(
            (p for p in printers if p.role == PrinterRole.RECEIPT and p.device_id == device_id),
            None,
        )
        receipt_printer = (
            {
                "printer_id": receipt.id,
                "connection": receipt.connection.value,
                "address": receipt.address,
            }
            if receipt is not None
            else None
        )

        # Active ONLINE-tender accounts for this branch (its own + every-branch).
        from app.services.payment_accounts import PaymentAccountService

        payment_accounts = [
            {
                "id": a.id,
                "label": a.label,
                "kind": a.kind.value,
                "account_name": a.account_name,
                "account_ref": a.account_ref,
                "bank_or_wallet": a.bank_or_wallet,
                "qr_payload": a.qr_payload,
            }
            for a in PaymentAccountService.active_for_branch(db, restaurant_id, branch_id)
        ]

        # Fingerprint the routing config (exclude runtime status/last_seen_at).
        core = {
            "stations": station_out,
            "printers": [
                {k: v for k, v in p.items() if k not in ("status", "last_seen_at")}
                for p in printer_out
            ],
            "category_map": category_out,
            "item_overrides": item_out,
            "payment_accounts": payment_accounts,
        }
        version = hashlib.sha256(
            json.dumps(core, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        payload = {
            "config_version": version,
            "stations": station_out,
            "printers": printer_out,
            "category_map": category_out,
            "item_overrides": item_out,
            "receipt_printer": receipt_printer,
            "payment_accounts": payment_accounts,
        }
        return payload, version
