"""Storing the event calendar, and answering what it means for a product.

`hijri.py` computes what happens in a year. This module owns everything after
that: keeping a tenant's calendar up to date, letting the Admin add and correct
events, and resolving "on this date, for this product, at this branch, what
multiplies the forecast?".

Two resolution rules are worth stating up front, because they are judgement calls
rather than arithmetic:

**Multipliers compound across events, and take the maximum within one event.**
Two overlapping events (a promo during Ramadan) genuinely stack, so they
multiply. But a product matching two tags of the SAME event — a samosa that is
both SNACK and IFTAR_ITEM during Ramadan — must not be multiplied twice for one
cause. It takes the larger of the two.

**The weekday dial belongs to the date, not the product.** An event's
`weekly_factor_weight` applies to every product at the branch, even one the event
does not otherwise move. During Ramadan the day's whole rhythm changes — nobody
eats at 2pm — so the normal weekday pattern is unreliable for tea as much as for
samosas, even though only samosas get a multiplier. Where several events overlap,
the lowest weight wins: the most disruptive event decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.models.calendar import (
    CalendarEvent,
    CalendarEventMultiplier,
    CalendarEventProductMultiplier,
    ProductEventTag,
)
from app.models.calendar_enums import EventSource, EventTag
from app.models.product import Product
from app.models.user import User
from app.services.audit import AuditService
from app.services.hijri import EventWindow, windows_for_year

NEUTRAL = Decimal("1.00")


@dataclass
class AppliedEvent:
    """One event's contribution, kept for the Admin-facing breakdown.

    The forecast shows the baseline and the event-adjusted number separately so a
    suggestion is never a black box. That is only possible if the reason is
    carried alongside the number rather than folded into it.
    """

    event_id: int
    name: str
    multiplier: Decimal
    is_estimated: bool
    #: "product" when this product has its own number for the event, "tag" when
    #: it fell back to its category. Shown to the Admin so "your setting for
    #: samosa" is distinguishable from "the iftar-item rule" — the same number
    #: means different things, and only one of them is theirs.
    source: str = "tag"
    #: The tag that matched, when source is "tag". Null for a product override.
    matched_tag: EventTag | None = None
    #: True when the product number is the system's proposal, not yet confirmed.
    is_proposed: bool = False


@dataclass
class EventFactor:
    """What the calendar says about one product on one date."""

    multiplier: Decimal = NEUTRAL
    weekly_factor_weight: Decimal = NEUTRAL
    applied: list[AppliedEvent] = field(default_factory=list)

    @property
    def is_estimated(self) -> bool:
        """True when any contributing event's dates are still unconfirmed."""
        return any(a.is_estimated for a in self.applied)


class EventCalendarService:
    # ----- generation ------------------------------------------------------

    @staticmethod
    def ensure_year(
        db: Session,
        *,
        restaurant_id: int,
        year: int,
        actor: User | None = None,
        commit: bool = True,
    ) -> dict:
        """Create or refresh this tenant's built-in events for a Gregorian year.

        Idempotent, and deliberately conservative about what it overwrites:

        * A confirmed lunar event (is_estimated cleared by the Admin) keeps its
          dates. The Admin saw the moon sighting announcement; a recomputation
          must never silently move Ramadan back to the calculated guess.
        * Multipliers are seeded only when the event is first created. Once the
          team has tuned "iftar items x3.2 because last year was not 3.5", a
          regeneration must not reset that work.
        """
        created = updated = skipped = 0
        for window in windows_for_year(year):
            existing = db.execute(
                select(CalendarEvent).where(
                    CalendarEvent.restaurant_id == restaurant_id,
                    CalendarEvent.key == window.key,
                    CalendarEvent.source_year == year,
                )
            ).scalar_one_or_none()

            if existing is None:
                EventCalendarService._create_from_window(
                    db, restaurant_id=restaurant_id, year=year, window=window
                )
                created += 1
                continue

            if not existing.is_estimated and existing.source is EventSource.LUNAR:
                # Confirmed by a human — the calculation does not get to argue.
                skipped += 1
                continue

            existing.name = window.name
            existing.starts_on = window.starts_on
            existing.ends_on = window.ends_on
            updated += 1

        db.flush()
        if actor is not None:
            AuditService.record(
                db,
                actor=actor,
                action="admin.calendar.generate",
                entity_type="calendar_year",
                entity_id=year,
                restaurant_id=restaurant_id,
                payload={"created": created, "updated": updated, "kept": skipped},
            )
        if commit:
            db.commit()
        return {"year": year, "created": created, "updated": updated, "kept": skipped}

    @staticmethod
    def _create_from_window(
        db: Session, *, restaurant_id: int, year: int, window: EventWindow
    ) -> CalendarEvent:
        event = CalendarEvent(
            restaurant_id=restaurant_id,
            key=window.key,
            source_year=year,
            name=window.name,
            source=window.source,
            starts_on=window.starts_on,
            ends_on=window.ends_on,
            is_estimated=window.is_estimated,
            weekly_factor_weight=window.weekly_factor_weight,
        )
        db.add(event)
        db.flush()
        for tag, multiplier in window.multipliers.items():
            db.add(
                CalendarEventMultiplier(
                    event_id=event.id, tag=tag, multiplier=multiplier
                )
            )
        db.flush()
        return event

    # ----- reads -----------------------------------------------------------

    @staticmethod
    def active_events(
        db: Session, *, restaurant_id: int, day: date, branch_id: int | None = None
    ) -> list[CalendarEvent]:
        """Events covering `day`, restaurant-wide plus this branch's own."""
        stmt = (
            select(CalendarEvent)
            .options(selectinload(CalendarEvent.multipliers))
            .where(
                CalendarEvent.restaurant_id == restaurant_id,
                CalendarEvent.starts_on <= day,
                CalendarEvent.ends_on >= day,
            )
        )
        rows = db.execute(stmt).scalars().all()
        # A branch-scoped event belongs to that branch alone — the match near one
        # viewing spot must not lift the whole chain.
        return [
            e
            for e in rows
            if e.branch_id is None or (branch_id is not None and e.branch_id == branch_id)
        ]

    @staticmethod
    def upcoming(
        db: Session,
        *,
        restaurant_id: int,
        start: date,
        end: date,
        branch_id: int | None = None,
    ) -> list[CalendarEvent]:
        """Events overlapping a window, for the Admin's "what's coming" strip."""
        rows = (
            db.execute(
                select(CalendarEvent)
                .options(selectinload(CalendarEvent.multipliers))
                .where(
                    CalendarEvent.restaurant_id == restaurant_id,
                    CalendarEvent.ends_on >= start,
                    CalendarEvent.starts_on <= end,
                )
                .order_by(CalendarEvent.starts_on)
            )
            .scalars()
            .all()
        )
        return [
            e
            for e in rows
            if e.branch_id is None or (branch_id is not None and e.branch_id == branch_id)
        ]

    @staticmethod
    def tags_for_products(
        db: Session, *, restaurant_id: int, product_ids: list[int]
    ) -> dict[int, set[EventTag]]:
        """Event tags per product — one query, for a whole forecast run."""
        if not product_ids:
            return {}
        rows = db.execute(
            select(ProductEventTag.product_id, ProductEventTag.tag).where(
                ProductEventTag.restaurant_id == restaurant_id,
                ProductEventTag.product_id.in_(product_ids),
            )
        ).all()
        out: dict[int, set[EventTag]] = {pid: set() for pid in product_ids}
        for product_id, tag in rows:
            out[product_id].add(tag)
        return out

    # ----- the resolution the forecast asks for ----------------------------

    @staticmethod
    def product_overrides(
        db: Session, *, event_ids: list[int]
    ) -> dict[tuple[int, int], tuple[Decimal, bool]]:
        """(event_id, product_id) -> (multiplier, is_proposed).

        Loaded for a whole day's events at once, so a forecast run over hundreds
        of products issues one query rather than one per product.
        """
        if not event_ids:
            return {}
        rows = db.execute(
            select(
                CalendarEventProductMultiplier.event_id,
                CalendarEventProductMultiplier.product_id,
                CalendarEventProductMultiplier.multiplier,
                CalendarEventProductMultiplier.is_proposed,
            ).where(CalendarEventProductMultiplier.event_id.in_(event_ids))
        ).all()
        return {
            (event_id, product_id): (Decimal(multiplier), bool(is_proposed))
            for event_id, product_id, multiplier, is_proposed in rows
        }

    @staticmethod
    def factor_from_events(
        events: list[CalendarEvent],
        tags: set[EventTag],
        *,
        product_id: int | None = None,
        overrides: dict[tuple[int, int], tuple[Decimal, bool]] | None = None,
    ) -> EventFactor:
        """Resolve pre-loaded events against one product.

        Separated from the query so a forecast run can load a day's events and
        overrides once, then resolve them against hundreds of products without
        re-reading anything.

        Precedence per event: the product's own number if it has one, otherwise
        its tag. A product-level number is an explicit statement about THIS dish
        and always beats the category rule it belongs to.
        """
        result = EventFactor()
        if not events:
            return result

        overrides = overrides or {}
        total = NEUTRAL
        weight = NEUTRAL
        for event in events:
            # The dial is a property of the DATE — it applies whether or not this
            # event moves this particular product. Lowest weight wins.
            weight = min(weight, Decimal(event.weekly_factor_weight))

            override = (
                overrides.get((event.id, product_id))
                if product_id is not None
                else None
            )
            if override is not None:
                best, is_proposed = override
                total *= best
                result.applied.append(
                    AppliedEvent(
                        event_id=event.id,
                        name=event.name,
                        multiplier=best,
                        is_estimated=event.is_estimated,
                        source="product",
                        is_proposed=is_proposed,
                    )
                )
                continue

            candidates = [
                m
                for m in event.multipliers
                # GENERAL is a wildcard: it matches every product, so a
                # whole-menu promo needs no tagging at all.
                if m.tag is EventTag.GENERAL or m.tag in tags
            ]
            if not candidates:
                continue
            # Max, not product: a samosa tagged SNACK and IFTAR_ITEM must not be
            # multiplied twice for the single fact that it is Ramadan.
            winner = max(candidates, key=lambda m: Decimal(m.multiplier))
            best = Decimal(winner.multiplier)
            total *= best
            result.applied.append(
                AppliedEvent(
                    event_id=event.id,
                    name=event.name,
                    multiplier=best,
                    is_estimated=event.is_estimated,
                    source="tag",
                    matched_tag=winner.tag,
                )
            )

        result.multiplier = total
        result.weekly_factor_weight = weight
        return result

    @staticmethod
    def factor_for(
        db: Session,
        *,
        restaurant_id: int,
        day: date,
        tags: set[EventTag],
        product_id: int | None = None,
        branch_id: int | None = None,
    ) -> EventFactor:
        """The single-product convenience form of `factor_from_events`."""
        events = EventCalendarService.active_events(
            db, restaurant_id=restaurant_id, day=day, branch_id=branch_id
        )
        overrides = (
            EventCalendarService.product_overrides(
                db, event_ids=[e.id for e in events]
            )
            if product_id is not None
            else {}
        )
        return EventCalendarService.factor_from_events(
            events, tags, product_id=product_id, overrides=overrides
        )

    # ----- per-product multipliers -----------------------------------------

    @staticmethod
    def set_product_multipliers(
        db: Session,
        *,
        actor: User,
        event_id: int,
        entries: dict[int, Decimal],
        is_proposed: bool = False,
        replace: bool = False,
        commit: bool = True,
    ) -> int:
        """Set exact multipliers for named products during one event.

        `replace=False` (the default) merges: it touches only the products named,
        leaving every other product's number alone. That is what an Admin editing
        two dishes expects. `replace=True` is for a wholesale re-set.
        """
        event = EventCalendarService.get_event(
            db, restaurant_id=actor.restaurant_id, event_id=event_id
        )

        product_ids = list(entries)
        if product_ids:
            owned = set(
                db.execute(
                    select(Product.id).where(
                        Product.id.in_(product_ids),
                        Product.restaurant_id == actor.restaurant_id,
                    )
                )
                .scalars()
                .all()
            )
            missing = set(product_ids) - owned
            if missing:
                raise NotFoundError(
                    f"Product(s) not found: {sorted(missing)}"
                )

        existing = {
            row.product_id: row
            for row in db.execute(
                select(CalendarEventProductMultiplier).where(
                    CalendarEventProductMultiplier.event_id == event.id
                )
            )
            .scalars()
            .all()
        }

        if replace:
            for product_id, row in existing.items():
                if product_id not in entries:
                    db.delete(row)

        for product_id, multiplier in entries.items():
            row = existing.get(product_id)
            if row is None:
                db.add(
                    CalendarEventProductMultiplier(
                        event_id=event.id,
                        product_id=product_id,
                        multiplier=multiplier,
                        is_proposed=is_proposed,
                    )
                )
            else:
                row.multiplier = multiplier
                row.is_proposed = is_proposed
        db.flush()

        AuditService.record(
            db,
            actor=actor,
            action="admin.calendar.product_multipliers",
            entity_type="calendar_event",
            entity_id=event.id,
            restaurant_id=actor.restaurant_id,
            after={
                "products": len(entries),
                "is_proposed": is_proposed,
                "replace": replace,
            },
        )
        if commit:
            db.commit()
        return len(entries)

    @staticmethod
    def list_product_multipliers(
        db: Session, *, restaurant_id: int, event_id: int
    ) -> list[dict]:
        event = EventCalendarService.get_event(
            db, restaurant_id=restaurant_id, event_id=event_id
        )
        rows = db.execute(
            select(
                CalendarEventProductMultiplier.product_id,
                CalendarEventProductMultiplier.multiplier,
                CalendarEventProductMultiplier.is_proposed,
                Product.name,
            )
            .join(Product, Product.id == CalendarEventProductMultiplier.product_id)
            .where(CalendarEventProductMultiplier.event_id == event.id)
            .order_by(Product.name)
        ).all()
        return [
            {
                "product_id": product_id,
                "product_name": name,
                "multiplier": str(multiplier),
                "is_proposed": bool(is_proposed),
            }
            for product_id, multiplier, is_proposed, name in rows
        ]

    @staticmethod
    def delete_product_multiplier(
        db: Session,
        *,
        actor: User,
        event_id: int,
        product_id: int,
        commit: bool = True,
    ) -> None:
        """Drop a product's own number so it falls back to its tag again."""
        event = EventCalendarService.get_event(
            db, restaurant_id=actor.restaurant_id, event_id=event_id
        )
        row = db.execute(
            select(CalendarEventProductMultiplier).where(
                CalendarEventProductMultiplier.event_id == event.id,
                CalendarEventProductMultiplier.product_id == product_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("No product multiplier set for that product.")
        db.delete(row)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="admin.calendar.product_multiplier_delete",
            entity_type="calendar_event",
            entity_id=event.id,
            restaurant_id=actor.restaurant_id,
            before={"product_id": product_id},
        )
        if commit:
            db.commit()

    # ----- product tagging -------------------------------------------------

    @staticmethod
    def set_product_tags(
        db: Session,
        *,
        actor: User,
        product_id: int,
        tags: list[EventTag],
        commit: bool = True,
    ) -> list[EventTag]:
        """Replace a product's event tags with exactly this set."""
        product = db.get(Product, product_id)
        if product is None or product.restaurant_id != actor.restaurant_id:
            raise NotFoundError("Product not found.")

        wanted = set(tags)
        existing = (
            db.execute(
                select(ProductEventTag).where(
                    ProductEventTag.product_id == product_id
                )
            )
            .scalars()
            .all()
        )
        for row in existing:
            if row.tag not in wanted:
                db.delete(row)
        have = {row.tag for row in existing}
        for tag in wanted - have:
            db.add(
                ProductEventTag(
                    restaurant_id=actor.restaurant_id,
                    product_id=product_id,
                    tag=tag,
                )
            )
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="admin.product.event_tags",
            entity_type="product",
            entity_id=product_id,
            restaurant_id=actor.restaurant_id,
            after={"tags": sorted(t.value for t in wanted)},
        )
        if commit:
            db.commit()
        return sorted(wanted, key=lambda t: t.value)

    # ----- admin writes ----------------------------------------------------

    @staticmethod
    def get_event(db: Session, *, restaurant_id: int, event_id: int) -> CalendarEvent:
        event = db.execute(
            select(CalendarEvent)
            .options(selectinload(CalendarEvent.multipliers))
            .where(
                CalendarEvent.id == event_id,
                CalendarEvent.restaurant_id == restaurant_id,
            )
        ).scalar_one_or_none()
        if event is None:
            raise NotFoundError("Calendar event not found.")
        return event

    @staticmethod
    def create_manual(
        db: Session, *, actor: User, body, commit: bool = True
    ) -> CalendarEvent:
        """Add an event no calendar can know about — a cricket final, a promo."""
        if body.ends_on < body.starts_on:
            raise ConflictError(
                "An event cannot end before it starts.", code="invalid_event_window"
            )
        event = CalendarEvent(
            restaurant_id=actor.restaurant_id,
            key=None,
            source_year=None,
            name=body.name,
            source=EventSource.MANUAL,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            # A human typed these dates, so they are not an estimate.
            is_estimated=False,
            weekly_factor_weight=body.weekly_factor_weight,
            branch_id=body.branch_id,
            note=body.note,
            created_by_id=actor.id,
        )
        db.add(event)
        db.flush()
        for tag, multiplier in (body.multipliers or {}).items():
            db.add(
                CalendarEventMultiplier(
                    event_id=event.id, tag=EventTag(tag), multiplier=multiplier
                )
            )
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="admin.calendar.create",
            entity_type="calendar_event",
            entity_id=event.id,
            restaurant_id=actor.restaurant_id,
            after={"name": event.name, "starts_on": event.starts_on.isoformat()},
        )
        if commit:
            db.commit()
        return EventCalendarService.get_event(
            db, restaurant_id=actor.restaurant_id, event_id=event.id
        )

    @staticmethod
    def update_event(
        db: Session, *, actor: User, event_id: int, body, commit: bool = True
    ) -> CalendarEvent:
        """Edit an event — including confirming a lunar one's real dates.

        Setting dates on a LUNAR event clears `is_estimated`: the Admin is stating
        the announced dates, which from then on survive any regeneration.
        """
        event = EventCalendarService.get_event(
            db, restaurant_id=actor.restaurant_id, event_id=event_id
        )
        before = {
            "starts_on": event.starts_on.isoformat(),
            "ends_on": event.ends_on.isoformat(),
            "is_estimated": event.is_estimated,
        }

        dates_changed = False
        if body.starts_on is not None:
            event.starts_on = body.starts_on
            dates_changed = True
        if body.ends_on is not None:
            event.ends_on = body.ends_on
            dates_changed = True
        if event.ends_on < event.starts_on:
            raise ConflictError(
                "An event cannot end before it starts.", code="invalid_event_window"
            )
        if body.name is not None:
            event.name = body.name
        if body.note is not None:
            event.note = body.note
        if body.weekly_factor_weight is not None:
            event.weekly_factor_weight = body.weekly_factor_weight
        if body.branch_id is not None:
            event.branch_id = body.branch_id
        if body.confirm or (dates_changed and event.source is EventSource.LUNAR):
            event.is_estimated = False

        if body.multipliers is not None:
            for row in list(event.multipliers):
                db.delete(row)
            db.flush()
            for tag, multiplier in body.multipliers.items():
                db.add(
                    CalendarEventMultiplier(
                        event_id=event.id, tag=EventTag(tag), multiplier=multiplier
                    )
                )

        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="admin.calendar.update",
            entity_type="calendar_event",
            entity_id=event.id,
            restaurant_id=actor.restaurant_id,
            before=before,
            after={
                "starts_on": event.starts_on.isoformat(),
                "ends_on": event.ends_on.isoformat(),
                "is_estimated": event.is_estimated,
            },
        )
        if commit:
            db.commit()
        return EventCalendarService.get_event(
            db, restaurant_id=actor.restaurant_id, event_id=event.id
        )

    @staticmethod
    def delete_event(
        db: Session, *, actor: User, event_id: int, commit: bool = True
    ) -> None:
        """Remove an event. Only a manual one — a generated event is part of the
        calendar, and deleting it would just come back on the next generation."""
        event = EventCalendarService.get_event(
            db, restaurant_id=actor.restaurant_id, event_id=event_id
        )
        if event.source is not EventSource.MANUAL:
            raise ConflictError(
                "Built-in events cannot be deleted; edit their dates or set their "
                "multipliers to 1 instead.",
                code="builtin_event_not_deletable",
            )
        db.delete(event)
        db.flush()
        AuditService.record(
            db,
            actor=actor,
            action="admin.calendar.delete",
            entity_type="calendar_event",
            entity_id=event_id,
            restaurant_id=actor.restaurant_id,
            before={"name": event.name},
        )
        if commit:
            db.commit()
