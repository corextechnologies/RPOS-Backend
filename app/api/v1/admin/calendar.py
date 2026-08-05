"""Admin routes for the event calendar (Phase 7, Stage 2).

Admin-only by design: the calendar decides what the forecast expects, and the
whole phase rests on the forecast being a suggestion the Admin owns. Kitchen and
Branch never see or edit it.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.db.session import get_db
from app.deps.auth import require_role
from app.models.calendar_enums import EventTag
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.calendar import (
    CalendarEventOut,
    CalendarEventUpdate,
    GenerateYearRequest,
    ManualEventCreate,
    ProductMultipliersUpdate,
    ProductTagsUpdate,
)
from app.services.event_calendar import EventCalendarService

router = APIRouter(dependencies=[Depends(require_role(UserRole.ADMIN))])


def _out(event) -> dict:
    return CalendarEventOut.model_validate(event).model_dump(mode="json")


@router.get("/calendar/tags")
def list_tags():
    """The fixed tag list products may be labelled with.

    Fixed on purpose: free-typed categories drift into "iftar-item" vs
    "Iftar Item" vs blank, and Ramadan then boosts half the menu.
    """
    return ok(
        [
            {
                "tag": t.value,
                "is_wildcard": t is EventTag.GENERAL,
            }
            for t in EventTag
        ]
    )


@router.post("/calendar/generate")
def generate_year(
    body: GenerateYearRequest,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Build this year's national holidays and Hijri-computed events.

    Safe to re-run: confirmed lunar dates and tuned multipliers are preserved.
    """
    result = EventCalendarService.ensure_year(
        db, restaurant_id=current.restaurant_id, year=body.year, actor=current
    )
    return ok(result)


@router.get("/calendar/events")
def list_events(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    branch_id: int | None = Query(default=None),
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Events overlapping a window — defaults to the next 90 days."""
    today = date.today()
    rows = EventCalendarService.upcoming(
        db,
        restaurant_id=current.restaurant_id,
        start=start or today,
        end=end or today + timedelta(days=90),
        branch_id=branch_id,
    )
    return ok([_out(e) for e in rows])


@router.get("/calendar/events/{event_id}")
def get_event(
    event_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    event = EventCalendarService.get_event(
        db, restaurant_id=current.restaurant_id, event_id=event_id
    )
    return ok(_out(event))


@router.post("/calendar/events")
def create_event(
    body: ManualEventCreate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    event = EventCalendarService.create_manual(db, actor=current, body=body)
    return ok(_out(event))


@router.patch("/calendar/events/{event_id}")
def update_event(
    event_id: int,
    body: CalendarEventUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    event = EventCalendarService.update_event(
        db, actor=current, event_id=event_id, body=body
    )
    return ok(_out(event))


@router.delete("/calendar/events/{event_id}", status_code=200)
def delete_event(
    event_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    EventCalendarService.delete_event(db, actor=current, event_id=event_id)
    return ok({"detail": "Event deleted."})


@router.get("/calendar/events/{event_id}/product-multipliers")
def list_product_multipliers(
    event_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Exact per-product numbers set for this event.

    A product listed here uses its own number; anything absent falls back to its
    tag, which is what keeps a newly added dish from silently getting no uplift.
    """
    rows = EventCalendarService.list_product_multipliers(
        db, restaurant_id=current.restaurant_id, event_id=event_id
    )
    return ok(rows)


@router.put("/calendar/events/{event_id}/product-multipliers")
def set_product_multipliers(
    event_id: int,
    body: ProductMultipliersUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Set exact multipliers for named products during this event.

    Merges by default — only the products named are touched. Send
    `replace: true` to clear every product not named.
    """
    count = EventCalendarService.set_product_multipliers(
        db,
        actor=current,
        event_id=event_id,
        entries=body.multipliers,
        is_proposed=body.is_proposed,
        replace=body.replace,
    )
    return ok(
        {
            "event_id": event_id,
            "updated": count,
            "products": EventCalendarService.list_product_multipliers(
                db, restaurant_id=current.restaurant_id, event_id=event_id
            ),
        }
    )


@router.delete(
    "/calendar/events/{event_id}/product-multipliers/{product_id}", status_code=200
)
def delete_product_multiplier(
    event_id: int,
    product_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    """Drop a product's own number so it falls back to its tag again."""
    EventCalendarService.delete_product_multiplier(
        db, actor=current, event_id=event_id, product_id=product_id
    )
    return ok({"detail": "Product multiplier removed."})


@router.get("/products/{product_id}/event-tags")
def get_product_tags(
    product_id: int,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    tags = EventCalendarService.tags_for_products(
        db, restaurant_id=current.restaurant_id, product_ids=[product_id]
    )
    return ok(
        {
            "product_id": product_id,
            "tags": sorted(t.value for t in tags.get(product_id, set())),
        }
    )


@router.put("/products/{product_id}/event-tags")
def set_product_tags(
    product_id: int,
    body: ProductTagsUpdate,
    current: User = Depends(require_role(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    tags = EventCalendarService.set_product_tags(
        db, actor=current, product_id=product_id, tags=body.tags
    )
    return ok({"product_id": product_id, "tags": [t.value for t in tags]})
