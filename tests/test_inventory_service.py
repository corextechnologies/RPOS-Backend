"""Phase 3 Slice 1 — InventoryService unit tests."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError
from app.models.inventory import InventoryItem, StockMovement, StockMovementType
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService


def test_receive_stock_creates_item_and_movement(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    item = InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=10,
        batch_code="B1",
        expiry_date=date.today() + timedelta(days=30),
        notes="initial intake",
    )
    db.flush()

    assert item.quantity == 10
    assert item.batch_code == "B1"
    assert item.expiry_date is not None

    movements = (
        db.execute(select(StockMovement).where(StockMovement.product_id == product.id))
        .scalars()
        .all()
    )
    assert len(movements) == 1
    assert movements[0].quantity_delta == 10
    assert movements[0].movement_type == StockMovementType.RECEIPT
    assert movements[0].actor_id == actor.id


def test_receive_stock_adds_to_existing_batch(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=5,
    )
    item = InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=7,
    )
    db.flush()

    assert item.quantity == 12
    count = db.execute(select(StockMovement)).scalars().all()
    assert len(count) == 2


def test_adjust_stock_positive_and_negative(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=20,
    )
    InventoryService.adjust_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity_delta=-5,
        notes="cycle count",
    )
    item = InventoryService.adjust_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity_delta=3,
    )
    db.flush()

    assert item.quantity == 18
    types = [
        m.movement_type
        for m in db.execute(select(StockMovement)).scalars().all()
    ]
    assert types.count(StockMovementType.ADJUSTMENT) == 2


def test_adjust_stock_rejects_negative_result(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=4,
    )
    with pytest.raises(ConflictError) as exc:
        InventoryService.adjust_stock(
            db,
            actor=actor,
            location_type=LocationType.WAREHOUSE,
            location_id=warehouse.id,
            product_id=product.id,
            quantity_delta=-10,
        )
    assert exc.value.code == "insufficient_stock"


def test_dispatch_decrements_and_links_request(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=15,
    )
    item = InventoryService.apply_dispatch(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=6,
        request_id=None,
        notes="dispatch to kitchen",
    )
    db.flush()

    assert item.quantity == 9
    movement = (
        db.execute(
            select(StockMovement).where(
                StockMovement.movement_type == StockMovementType.DISPATCH
            )
        )
        .scalar_one()
    )
    assert movement.quantity_delta == -6


def test_dispatch_rejects_insufficient_stock(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=2,
    )
    with pytest.raises(ConflictError) as exc:
        InventoryService.apply_dispatch(
            db,
            actor=actor,
            location_type=LocationType.WAREHOUSE,
            location_id=warehouse.id,
            product_id=product.id,
            quantity=5,
        )
    assert exc.value.code == "insufficient_stock"


def test_waste_and_expiry(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)
    actor = setup["warehouse_mgr"]

    InventoryService.receive_stock(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=10,
        batch_code="EXP",
    )
    InventoryService.mark_waste_or_expiry(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=3,
        movement_type=StockMovementType.WASTE,
        batch_code="EXP",
        notes="damaged",
    )
    item = InventoryService.mark_waste_or_expiry(
        db,
        actor=actor,
        location_type=LocationType.WAREHOUSE,
        location_id=warehouse.id,
        product_id=product.id,
        quantity=2,
        movement_type=StockMovementType.EXPIRY,
        batch_code="EXP",
    )
    db.flush()

    assert item.quantity == 5
    rows = db.execute(select(InventoryItem)).scalars().all()
    assert len(rows) == 1


def test_cross_restaurant_product_rejected(
    db, restaurant_setup, make_restaurant, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    other = make_restaurant("Other Co")
    foreign_product = make_product(other.id, name="Foreign")
    actor = setup["warehouse_mgr"]

    with pytest.raises(NotFoundError):
        InventoryService.receive_stock(
            db,
            actor=actor,
            location_type=LocationType.WAREHOUSE,
            location_id=warehouse.id,
            product_id=foreign_product.id,
            quantity=1,
        )


def test_receive_rejects_non_positive_quantity(
    db, restaurant_setup, make_warehouse, make_product
):
    setup = restaurant_setup
    warehouse = make_warehouse(setup["restaurant"].id)
    product = make_product(setup["restaurant"].id)

    with pytest.raises(ConflictError) as exc:
        InventoryService.receive_stock(
            db,
            actor=setup["warehouse_mgr"],
            location_type=LocationType.WAREHOUSE,
            location_id=warehouse.id,
            product_id=product.id,
            quantity=0,
        )
    assert exc.value.code == "invalid_quantity"
