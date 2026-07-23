"""Full production-target lifecycle: Admin sets it, Kitchen produces and ships
it, each Branch receives its slice — with stock moving at complete, dispatch and
receive, and the target rolling up to RECEIVED once every allocation lands.
"""
import pytest

from app.models.enums import UserRole
from app.models.product import ProductKind
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers


@pytest.fixture
def pt_ctx(restaurant_setup, make_product, make_branch, make_user, db):
    r = restaurant_setup["restaurant"]
    burger = make_product(r.id, name="Burger", sku="BG-1",
                          kind=ProductKind.FINISHED_GOOD)
    coke = make_product(r.id, name="Coke", sku="CK-1", kind=ProductKind.RESALE)

    branch2 = make_branch(r.id, name="Branch Two", location="Loc 2")
    make_user(
        "branch2@test.com", UserRole.BRANCH_MANAGER,
        restaurant_id=r.id, branch_id=branch2.id,
    )

    # Resale stock is already held at the kitchen (nobody produces it).
    InventoryService.receive_stock(
        db,
        actor=restaurant_setup["kitchen_mgr"],
        location_type=LocationType.KITCHEN,
        location_id=restaurant_setup["home_kitchen"].id,
        product_id=coke.id,
        quantity=50,
    )
    db.flush()
    return {
        **restaurant_setup,
        "burger": burger,
        "coke": coke,
        "branch1": restaurant_setup["home_branch"],
        "branch2": branch2,
        "kitchen": restaurant_setup["home_kitchen"],
    }


def _on_hand(db, ctx, location_type, location_id, product_id):
    stock = {
        item.product_id: item.quantity
        for item, _ in InventoryService.list_for_location(
            db,
            restaurant_id=ctx["restaurant"].id,
            location_type=location_type,
            location_id=location_id,
        )
    }
    return stock.get(product_id, 0)


def _create_target(client, ah, ctx, lines):
    return client.post(
        "/v1/admin/production-targets",
        json={
            "kitchen_id": ctx["kitchen"].id,
            "target_date": "2026-07-23",
            "lines": lines,
        },
        headers=ah,
    )


def test_full_lifecycle_moves_stock_and_rolls_up(client, pt_ctx, db):
    ah = auth_headers(client, "admin@test.com")
    kh = auth_headers(client, "kitchen@test.com")
    b1 = auth_headers(client, "branch@test.com")
    b2 = auth_headers(client, "branch2@test.com")
    burger, coke = pt_ctx["burger"], pt_ctx["coke"]

    # 1. Admin sets a target: make 30 burgers, set aside 10 cokes.
    created = _create_target(client, ah, pt_ctx, [
        {"product_id": burger.id, "quantity": 30},
        {"product_id": coke.id, "quantity": 10},
    ])
    assert created.status_code == 200, created.text
    d = created.json()["data"]
    tid = d["id"]
    assert d["status"] == "PENDING"
    # Line kind is surfaced so the kitchen UI can split made vs resale.
    kinds = {ln["product_id"]: ln["kind"] for ln in d["lines"]}
    assert kinds[burger.id] == "FINISHED_GOOD"
    assert kinds[coke.id] == "RESALE"
    assert all(ln["produced"] is False for ln in d["lines"])
    line_by_pid = {ln["product_id"]: ln["id"] for ln in d["lines"]}

    # 2. Kitchen acknowledges, then starts production.
    assert client.post(
        f"/v1/kitchen/production-targets/{tid}/acknowledge", headers=kh
    ).json()["data"]["status"] == "ACKNOWLEDGED"
    started = client.post(f"/v1/kitchen/production-targets/{tid}/start", headers=kh)
    assert started.status_code == 200, started.text
    assert started.json()["data"]["status"] == "IN_PRODUCTION"

    # 3. Complete is rejected until every line is produced.
    early = client.post(f"/v1/kitchen/production-targets/{tid}/complete", headers=kh)
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "target_lines_not_produced"

    # 4. Mark each line produced.
    for pid in (burger.id, coke.id):
        r = client.post(
            f"/v1/kitchen/production-targets/{tid}/lines/{line_by_pid[pid]}/produced",
            headers=kh,
        )
        assert r.status_code == 200, r.text
    marked = client.get(f"/v1/kitchen/production-targets/{tid}", headers=kh).json()["data"]
    assert all(ln["produced"] is True for ln in marked["lines"])

    # 5. Complete — credits the kitchen's finished goods (burgers), not resale.
    completed = client.post(
        f"/v1/kitchen/production-targets/{tid}/complete", headers=kh
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "COMPLETED"
    assert _on_hand(db, pt_ctx, LocationType.KITCHEN, pt_ctx["kitchen"].id, burger.id) == 30
    assert _on_hand(db, pt_ctx, LocationType.KITCHEN, pt_ctx["kitchen"].id, coke.id) == 50

    # 6. Admin allocates: 20 burgers -> b1, 10 burgers -> b2, 10 cokes -> b1.
    alloc = client.post(
        f"/v1/admin/production-targets/{tid}/allocate",
        json={"allocations": [
            {"line_id": line_by_pid[burger.id], "branch_id": pt_ctx["branch1"].id, "quantity": 20},
            {"line_id": line_by_pid[burger.id], "branch_id": pt_ctx["branch2"].id, "quantity": 10},
            {"line_id": line_by_pid[coke.id], "branch_id": pt_ctx["branch1"].id, "quantity": 10},
        ]},
        headers=ah,
    )
    assert alloc.status_code == 200, alloc.text
    ad = alloc.json()["data"]
    assert ad["status"] == "ALLOCATED"
    assert len(ad["allocations"]) == 3
    assert {a["status"] for a in ad["allocations"]} == {"ALLOCATED"}

    # 7. Kitchen dispatches — debits the kitchen for every allocated unit.
    disp = client.post(f"/v1/kitchen/production-targets/{tid}/dispatch", headers=kh)
    assert disp.status_code == 200, disp.text
    dd = disp.json()["data"]
    assert dd["status"] == "DISPATCHED"
    assert {a["status"] for a in dd["allocations"]} == {"DISPATCHED"}
    assert _on_hand(db, pt_ctx, LocationType.KITCHEN, pt_ctx["kitchen"].id, burger.id) == 0
    assert _on_hand(db, pt_ctx, LocationType.KITCHEN, pt_ctx["kitchen"].id, coke.id) == 40

    # 8. Both branches see their slices on the existing Incoming screen.
    inbox1 = client.get("/v1/branch/deliveries", headers=b1).json()["data"]
    assert {row["request_id"] for row in inbox1} == {tid}
    assert {row["product_id"] for row in inbox1} == {burger.id, coke.id}
    assert all(row["from_label"] == pt_ctx["kitchen"].name for row in inbox1)

    # 9. Each branch receives its allocations — crediting its own stock.
    for row in inbox1:
        rec = client.post(f"/v1/branch/deliveries/{row['id']}/receive", headers=b1)
        assert rec.status_code == 200, rec.text
    assert _on_hand(db, pt_ctx, LocationType.BRANCH, pt_ctx["branch1"].id, burger.id) == 20
    assert _on_hand(db, pt_ctx, LocationType.BRANCH, pt_ctx["branch1"].id, coke.id) == 10

    # Target is still DISPATCHED until branch2's slice lands too.
    mid = client.get(f"/v1/admin/production-targets/{tid}", headers=ah).json()["data"]
    assert mid["status"] == "DISPATCHED"

    inbox2 = client.get("/v1/branch/deliveries", headers=b2).json()["data"]
    assert len(inbox2) == 1
    rec2 = client.post(f"/v1/branch/deliveries/{inbox2[0]['id']}/receive", headers=b2)
    assert rec2.status_code == 200, rec2.text
    assert _on_hand(db, pt_ctx, LocationType.BRANCH, pt_ctx["branch2"].id, burger.id) == 10

    # 10. Last allocation received -> target rolls up to RECEIVED.
    final = client.get(f"/v1/admin/production-targets/{tid}", headers=ah).json()["data"]
    assert final["status"] == "RECEIVED"
    assert {a["status"] for a in final["allocations"]} == {"RECEIVED"}


def test_bad_transitions_return_409(client, pt_ctx):
    ah = auth_headers(client, "admin@test.com")
    kh = auth_headers(client, "kitchen@test.com")
    burger = pt_ctx["burger"]

    tid = _create_target(client, ah, pt_ctx, [
        {"product_id": burger.id, "quantity": 5},
    ]).json()["data"]["id"]

    # start before acknowledge
    r = client.post(f"/v1/kitchen/production-targets/{tid}/start", headers=kh)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_target_status"

    # complete before start
    client.post(f"/v1/kitchen/production-targets/{tid}/acknowledge", headers=kh)
    r = client.post(f"/v1/kitchen/production-targets/{tid}/complete", headers=kh)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_target_status"

    # dispatch before allocate
    r = client.post(f"/v1/kitchen/production-targets/{tid}/dispatch", headers=kh)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_target_status"

    # allocate before complete
    r = client.post(
        f"/v1/admin/production-targets/{tid}/allocate",
        json={"allocations": [
            {"line_id": 1, "branch_id": pt_ctx["branch1"].id, "quantity": 1}]},
        headers=ah,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_target_status"


def _advance_to_completed(client, ah, kh, pt_ctx, lines):
    tid = _create_target(client, ah, pt_ctx, lines).json()["data"]["id"]
    d = client.get(f"/v1/kitchen/production-targets/{tid}", headers=kh).json()["data"]
    client.post(f"/v1/kitchen/production-targets/{tid}/acknowledge", headers=kh)
    client.post(f"/v1/kitchen/production-targets/{tid}/start", headers=kh)
    for ln in d["lines"]:
        client.post(
            f"/v1/kitchen/production-targets/{tid}/lines/{ln['id']}/produced",
            headers=kh,
        )
    client.post(f"/v1/kitchen/production-targets/{tid}/complete", headers=kh)
    return tid, {ln["product_id"]: ln["id"] for ln in d["lines"]}


def test_allocate_over_produced_is_rejected(client, pt_ctx):
    ah = auth_headers(client, "admin@test.com")
    kh = auth_headers(client, "kitchen@test.com")
    burger = pt_ctx["burger"]

    tid, line_by_pid = _advance_to_completed(
        client, ah, kh, pt_ctx, [{"product_id": burger.id, "quantity": 10}]
    )
    r = client.post(
        f"/v1/admin/production-targets/{tid}/allocate",
        json={"allocations": [
            {"line_id": line_by_pid[burger.id], "branch_id": pt_ctx["branch1"].id, "quantity": 7},
            {"line_id": line_by_pid[burger.id], "branch_id": pt_ctx["branch2"].id, "quantity": 5},
        ]},
        headers=ah,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "target_allocation_exceeds_produced"


def test_allocate_foreign_branch_is_404(client, pt_ctx, make_restaurant, make_branch):
    ah = auth_headers(client, "admin@test.com")
    kh = auth_headers(client, "kitchen@test.com")
    burger = pt_ctx["burger"]

    other = make_restaurant("Other Co")
    foreign = make_branch(other.id, name="Foreign", location="Elsewhere")

    tid, line_by_pid = _advance_to_completed(
        client, ah, kh, pt_ctx, [{"product_id": burger.id, "quantity": 10}]
    )
    r = client.post(
        f"/v1/admin/production-targets/{tid}/allocate",
        json={"allocations": [
            {"line_id": line_by_pid[burger.id], "branch_id": foreign.id, "quantity": 5}]},
        headers=ah,
    )
    assert r.status_code == 404


def test_dispatch_insufficient_stock_leaves_status_untouched(client, pt_ctx, db):
    ah = auth_headers(client, "admin@test.com")
    kh = auth_headers(client, "kitchen@test.com")
    burger = pt_ctx["burger"]

    tid, line_by_pid = _advance_to_completed(
        client, ah, kh, pt_ctx, [{"product_id": burger.id, "quantity": 10}]
    )
    client.post(
        f"/v1/admin/production-targets/{tid}/allocate",
        json={"allocations": [
            {"line_id": line_by_pid[burger.id], "branch_id": pt_ctx["branch1"].id, "quantity": 10}]},
        headers=ah,
    )

    # Drain the kitchen's produced burgers so dispatch can't cover the batch.
    InventoryService.apply_dispatch_fefo(
        db,
        actor=pt_ctx["kitchen_mgr"],
        location_type=LocationType.KITCHEN,
        location_id=pt_ctx["kitchen"].id,
        product_id=burger.id,
        quantity=10,
    )
    db.flush()

    r = client.post(f"/v1/kitchen/production-targets/{tid}/dispatch", headers=kh)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "insufficient_stock"
    # Status untouched — still ALLOCATED, allocations still ALLOCATED.
    after = client.get(f"/v1/admin/production-targets/{tid}", headers=ah).json()["data"]
    assert after["status"] == "ALLOCATED"
    assert {a["status"] for a in after["allocations"]} == {"ALLOCATED"}


def test_kitchen_scoping_hides_other_kitchen_target(
    client, pt_ctx, make_kitchen, make_user
):
    ah = auth_headers(client, "admin@test.com")
    r = pt_ctx["restaurant"]
    other_kitchen = make_kitchen(r.id, name="Kitchen Two", location="K2")
    make_user(
        "kitchen2@test.com", UserRole.KITCHEN_MANAGER,
        restaurant_id=r.id, kitchen_id=other_kitchen.id,
    )
    kh2 = auth_headers(client, "kitchen2@test.com")

    tid = _create_target(client, ah, pt_ctx, [
        {"product_id": pt_ctx["burger"].id, "quantity": 5},
    ]).json()["data"]["id"]

    # A manager at a different kitchen can't see or act on it.
    assert client.get(
        f"/v1/kitchen/production-targets/{tid}", headers=kh2
    ).status_code == 404
    assert client.post(
        f"/v1/kitchen/production-targets/{tid}/acknowledge", headers=kh2
    ).status_code == 404
