"""Public, unauthenticated endpoints — the QR live-menu.

Everything here serves anonymous requests (no token). Project only public-safe
fields: no cost prices, modifiers, or combo internals.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.responses import ok
from app.db.session import get_db
from app.models.restaurant import Restaurant
from app.services.menu import MenuService

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/menu/{slug}")
def public_menu(slug: str, db: Session = Depends(get_db)):
    """The currently published menu for the restaurant behind `slug`.

    404 when the slug is unknown OR the restaurant has no published menu — the
    frontend renders a "menu not published yet" state for both.
    """
    restaurant = db.execute(
        select(Restaurant).where(Restaurant.public_slug == slug)
    ).scalar_one_or_none()
    if restaurant is None:
        raise NotFoundError("Menu not found.")

    # Reuses the same published-version resolution as the authenticated
    # GET /pos/menu — just anonymous and trimmed. Raises NotFoundError (404)
    # when nothing is published yet.
    menu = MenuService.published(db, restaurant.id)

    items = [
        {
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "price_minor": item.price_minor,
            # No branch context on the public menu — a published item is
            # listed as available; branch-level 86'ing is a POS concern.
            "is_available": True,
            "image_url": item.image_url,
        }
        for item in sorted(menu.items, key=lambda i: (i.sort_order, i.id))
    ]

    return ok(
        {
            "restaurant_name": restaurant.name,
            "logo_url": restaurant.logo_url,
            "currency": menu.currency,
            "items": items,
        }
    )
