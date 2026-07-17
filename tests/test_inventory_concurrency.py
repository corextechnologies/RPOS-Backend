"""Phase 5.1 — the oversell fix (SELECT ... FOR UPDATE on the on-hand row).

The normal `db` fixture pins a single connection, so it can never observe a race.
This module talks to the session-scoped `engine` directly with two independent
sessions and real commits, then cleans up after itself (its rows are committed,
so the per-test rollback net does not cover them).
"""
import inspect
import threading
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.inventory import InventoryItem
from app.models.enums import UserRole
from app.models.location import Branch
from app.models.product import Product
from app.models.request_enums import LocationType
from app.models.restaurant import Restaurant
from app.models.user import User
from app.services.inventory import InventoryService


def test_apply_delta_locks_row_for_update():
    """Cheap, DB-free guard: the lock must not get 'cleaned up' out of the code."""
    assert "for_update=True" in inspect.getsource(InventoryService._apply_delta)
    assert "with_for_update" in inspect.getsource(InventoryService._get_item)


def _cleanup(engine, rid):
    # Only the tables this test actually writes, in FK-dependency order. All of
    # these carry restaurant_id; deleting the restaurant last cascades anyway,
    # but explicit deletes keep it readable and order-safe.
    with engine.begin() as conn:
        for table in ("stock_movements", "inventory_items", "products", "users", "branches"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE restaurant_id = :rid"), {"rid": rid}
            )
        conn.execute(text("DELETE FROM restaurants WHERE id = :rid"), {"rid": rid})


def test_concurrent_dispatch_does_not_oversell(engine):
    suffix = uuid.uuid4().hex[:8]

    setup = Session(bind=engine, future=True)
    try:
        r = Restaurant(name=f"conc-{suffix}")
        setup.add(r)
        setup.flush()
        rid = r.id
        b = Branch(restaurant_id=rid, name=f"br-{suffix}")
        setup.add(b)
        setup.flush()
        p = Product(restaurant_id=rid, name=f"prod-{suffix}")
        setup.add(p)
        setup.flush()
        actor = User(
            email=f"conc-{suffix}@test.com",
            hashed_password="x",
            role=UserRole.BRANCH_MANAGER,
            restaurant_id=rid,
            branch_id=b.id,
        )
        setup.add(actor)
        setup.flush()
        item = InventoryItem(
            restaurant_id=rid,
            location_type=LocationType.BRANCH,
            location_id=b.id,
            product_id=p.id,
            quantity=10,
            batch_code="",
        )
        setup.add(item)
        setup.flush()
        setup.commit()
        bid, pid, aid, iid = b.id, p.id, actor.id, item.id
    finally:
        setup.close()

    results: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        s = Session(bind=engine, future=True)
        try:
            actor = s.get(User, aid)
            barrier.wait(timeout=15)
            try:
                InventoryService.apply_dispatch(
                    s,
                    actor=actor,
                    location_type=LocationType.BRANCH,
                    location_id=bid,
                    product_id=pid,
                    quantity=6,
                )
                s.commit()
                results[name] = "ok"
            except ConflictError as exc:
                s.rollback()
                results[name] = exc.code
            except Exception as exc:  # pragma: no cover - surfaces unexpected errors
                s.rollback()
                results[name] = f"err:{type(exc).__name__}"
        finally:
            s.close()

    try:
        threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)

        # Exactly one dispatch of 6 succeeds; the other is refused. Without the
        # row lock both would read 10 and both commit — 12 dispatched from 10.
        assert sorted(results.values()) == ["insufficient_stock", "ok"], results

        check = Session(bind=engine, future=True)
        try:
            final = check.get(InventoryItem, iid)
            assert final.quantity == 4, final.quantity
        finally:
            check.close()
    finally:
        _cleanup(engine, rid)
