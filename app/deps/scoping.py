"""Central multi-tenancy + hierarchy scoping.

Every controller must route reads through these helpers instead of writing its
own filter — this is the single place the "who can see whom" rule lives.
"""
from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.user import User


def apply_tenant_scope(stmt: Select, user: User, model) -> Select:
    """Restrict a select over a tenant-scoped model to the user's restaurant.

    Every role — including SUPER_ADMIN — is filtered by restaurant_id. The Super
    Admin is the POS owner: it manages Admins and billing, but never sees a
    restaurant's *operational* data (branches, kitchens, warehouses, products,
    sub-users). Since a Super Admin has no restaurant_id, this yields no rows for
    them by design — operational portals are Admin/Manager only.
    """
    return stmt.where(model.restaurant_id == user.restaurant_id)


def _descendant_user_ids(db: Session, root_id: int) -> list[int]:
    """All user ids in the created-by subtree rooted at root_id (recursive CTE)."""
    base = (
        select(User.id)
        .where(User.created_by_id == root_id)
        .cte(name="subtree", recursive=True)
    )
    child = select(User.id).join(base, User.created_by_id == base.c.id)
    tree = base.union_all(child)
    return list(db.execute(select(tree.c.id)).scalars().all())


def visible_users(db: Session, user: User) -> Select:
    """Return a Select of the User rows this user is allowed to see.

    - SUPER_ADMIN  -> only ADMIN users (the restaurant owners it manages); it
                      never sees managers, sub-users, or anything inside a
                      restaurant.
    - ADMIN        -> every user in their own restaurant (their whole hierarchy)
    - Manager      -> only the users in their created-by subtree
    """
    if user.role == UserRole.SUPER_ADMIN:
        return select(User).where(User.role == UserRole.ADMIN)

    if user.role == UserRole.ADMIN:
        return select(User).where(User.restaurant_id == user.restaurant_id)

    # Manager roles: their own created subtree only.
    ids = _descendant_user_ids(db, user.id)
    if not ids:
        # Guard against an empty IN () which some backends dislike.
        return select(User).where(User.id == -1)
    return select(User).where(User.id.in_(ids))
