"""POS-2/3/4/5/6 — money, control, sync, recipes, and the pack abstraction."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.enums import BranchPosition, UserRole
from app.models.request_enums import LocationType
from app.services.inventory import InventoryService
from tests.conftest import auth_headers
from tests.test_pos_sell import _pos_headers, sell_ctx  # noqa: F401 (fixture reuse)


def _create(client, headers, item_id, qty=1, **kw):
    body = {"local_id": uuid.uuid4().hex,
            "lines": [{"menu_item_id": item_id, "quantity": qty}], **kw}
    return client.post("/v1/pos/orders", json=body,
                       headers={**headers, "Idempotency-Key": uuid.uuid4().hex})


def _idem(headers):
    return {**headers, "Idempotency-Key": uuid.uuid4().hex}


# ---- POS-2: money ----------------------------------------------------------

def test_cash_payment_computes_change_and_settles(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]  # 500.00
    client.post("/v1/pos/shifts", json={"opening_float_minor": 100000}, headers=headers)

    resp = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CASH", "amount_minor": 50000, "tendered_minor": 100000},
        headers=_idem(headers),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["change_minor"] == 50000  # 1000.00 tendered for a 500.00 bill
    assert data["status"] == "CAPTURED"


def test_split_payment_across_two_tenders(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"], qty=2).json()["data"]  # 1000.00

    first = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CARD", "amount_minor": 60000},
        headers=_idem(headers),
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CASH", "amount_minor": 40000, "tendered_minor": 40000},
        headers=_idem(headers),
    )
    assert second.status_code == 200, second.text
    # Now fully paid — a third tender has nothing left to pay.
    third = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CASH", "amount_minor": 100, "tendered_minor": 100},
        headers=_idem(headers),
    )
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "overpayment"


def test_overpayment_is_refused_with_the_amount_due(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    resp = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CARD", "amount_minor": 999999},
        headers=_idem(headers),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "overpayment"
    assert resp.json()["error"]["details"]["due_minor"] == 50000


def test_cash_needs_the_tendered_amount(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    resp = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CASH", "amount_minor": 50000},
        headers=_idem(headers),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "tender_required"


def test_order_taker_cannot_take_cash(client, sell_ctx, make_user):
    """The curbside profile: no drawer, so no cash — however senior the user."""
    headers = _pos_headers(client, sell_ctx, make_user,
                           position=BranchPosition.ORDER_TAKER)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    cash = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CASH", "amount_minor": 50000, "tendered_minor": 50000},
        headers=_idem(headers),
    )
    assert cash.status_code == 403
    assert cash.json()["error"]["code"] == "position_forbidden"
    # ...but they still cannot take card either (PAYMENT_TAKE is not theirs).
    card = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CARD", "amount_minor": 50000},
        headers=_idem(headers),
    )
    assert card.status_code == 403


def test_salesperson_can_take_card_but_not_cash(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user,
                           position=BranchPosition.SALESPERSON)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    card = client.post(
        f"/v1/pos/orders/{order['id']}/payments",
        json={"method": "CARD", "amount_minor": 50000},
        headers=_idem(headers),
    )
    assert card.status_code == 200, card.text

    order2 = _create(client, headers, sell_ctx["burger"]).json()["data"]
    cash = client.post(
        f"/v1/pos/orders/{order2['id']}/payments",
        json={"method": "CASH", "amount_minor": 50000, "tendered_minor": 50000},
        headers=_idem(headers),
    )
    assert cash.status_code == 403


def test_price_quote_has_no_side_effects(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    for method in ("CASH", "CARD"):
        quote = client.post(
            f"/v1/pos/price-quote/{order['id']}",
            json={"payment_method": method}, headers=headers,
        )
        assert quote.status_code == 200, quote.text
        assert quote.json()["data"]["grand_total_minor"] == 50000
    # Nothing was paid by asking.
    assert client.get(f"/v1/pos/orders/{order['id']}",
                      headers=headers).json()["data"]["status"] == "DRAFT"


def test_refund_is_capped_at_what_was_paid(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    mgr_headers = _mgr_pos(client, sell_ctx)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    client.post(f"/v1/pos/orders/{order['id']}/payments",
                json={"method": "CARD", "amount_minor": 50000},
                headers=_idem(headers))

    over = client.post(
        "/v1/pos/refunds",
        json={"order_id": order["id"], "amount_minor": 99999, "method": "CARD",
              "reason_code": "KITCHEN_ERROR"},
        headers=_idem(mgr_headers),
    )
    assert over.status_code == 409
    assert over.json()["error"]["code"] == "over_refund"

    ok_refund = client.post(
        "/v1/pos/refunds",
        json={"order_id": order["id"], "amount_minor": 50000, "method": "CARD",
              "reason_code": "KITCHEN_ERROR"},
        headers=_idem(mgr_headers),
    )
    assert ok_refund.status_code == 200, ok_refund.text


def test_staff_cannot_refund(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    client.post(f"/v1/pos/orders/{order['id']}/payments",
                json={"method": "CARD", "amount_minor": 50000},
                headers=_idem(headers))
    resp = client.post(
        "/v1/pos/refunds",
        json={"order_id": order["id"], "amount_minor": 100, "method": "CARD",
              "reason_code": "OTHER"},
        headers=_idem(headers),
    )
    assert resp.status_code == 403


def _mgr_pos(client, sell_ctx):
    """A branch manager signed in on the POS terminal."""
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "branch@test.com", "password": "Pass@1234",
              "device_uid": sell_ctx["device_uid"]},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def test_list_discount_rules(client, sell_ctx):
    admin = auth_headers(client, "admin@test.com")

    # Empty at first.
    resp = client.get("/v1/pos/discount-rules", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["data"] == []

    # Create two rules.
    client.post("/v1/pos/discount-rules",
                json={"code": "HAPPY10", "name": "Happy Hour 10%", "type": "PCT",
                      "value_bp": 1000, "max_pct_bp": 1000},
                headers=admin)
    client.post("/v1/pos/discount-rules",
                json={"code": "FLAT5", "name": "Flat $5 Off", "type": "AMT",
                      "scope": "LINE", "amount_minor": 500, "max_pct_bp": 5000},
                headers=admin)

    resp = client.get("/v1/pos/discount-rules", headers=admin)
    assert resp.status_code == 200
    rules = resp.json()["data"]
    assert len(rules) == 2
    assert rules[0]["code"] == "HAPPY10"
    assert rules[0]["name"] == "Happy Hour 10%"
    assert rules[0]["type"] == "PCT"
    assert rules[0]["is_active"] is True
    assert rules[1]["code"] == "FLAT5"
    assert rules[1]["scope"] == "LINE"
    assert rules[1]["amount_minor"] == 500


def test_update_discount_rule(client, sell_ctx):
    admin = auth_headers(client, "admin@test.com")

    # Create a rule.
    created = client.post("/v1/pos/discount-rules",
                          json={"code": "STAFF10", "name": "Staff 10%", "type": "PCT",
                                "value_bp": 1000, "max_pct_bp": 1000},
                          headers=admin)
    rule_id = created.json()["data"]["id"]

    # Partial update — only name and value_bp.
    resp = client.patch(f"/v1/pos/discount-rules/{rule_id}",
                        json={"name": "Staff 15%", "value_bp": 1500},
                        headers=admin)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "Staff 15%"
    assert data["value_bp"] == 1500
    assert data["code"] == "STAFF10"       # unchanged
    assert data["max_pct_bp"] == 1000      # unchanged
    assert data["is_active"] is True       # unchanged

    # Deactivate.
    resp = client.patch(f"/v1/pos/discount-rules/{rule_id}",
                        json={"is_active": False}, headers=admin)
    assert resp.status_code == 200
    assert resp.json()["data"]["is_active"] is False

    # Not found for non-existent id.
    resp = client.patch("/v1/pos/discount-rules/999999",
                        json={"name": "X"}, headers=admin)
    assert resp.status_code == 404


def test_delete_discount_rule(client, sell_ctx):
    admin = auth_headers(client, "admin@test.com")

    # Create a rule then delete it.
    created = client.post("/v1/pos/discount-rules",
                          json={"code": "TMP", "name": "Temporary", "type": "PCT",
                                "value_bp": 500},
                          headers=admin)
    rule_id = created.json()["data"]["id"]

    resp = client.delete(f"/v1/pos/discount-rules/{rule_id}", headers=admin)
    assert resp.status_code == 200

    # Gone from the list.
    rules = client.get("/v1/pos/discount-rules", headers=admin).json()["data"]
    assert all(r["id"] != rule_id for r in rules)

    # Delete again → 404.
    resp = client.delete(f"/v1/pos/discount-rules/{rule_id}", headers=admin)
    assert resp.status_code == 404


def test_discount_rule_scheduling_fields_roundtrip(client, sell_ctx):
    admin = auth_headers(client, "admin@test.com")

    resp = client.post("/v1/pos/discount-rules", json={
        "code": "HAPPY", "name": "Happy Hour", "type": "PCT", "value_bp": 2000,
        "valid_from": "2026-03-01T00:00:00Z",
        "valid_to": "2026-03-31T23:59:59Z",
        "active_days": ["mon", "tue", "wed", "thu", "fri"],
        "active_hours_start": "14:00:00",
        "active_hours_end": "17:00:00",
    }, headers=admin)
    assert resp.status_code == 200, resp.text
    rule_id = resp.json()["data"]["id"]

    # GET returns all scheduling fields.
    resp = client.get("/v1/pos/discount-rules", headers=admin)
    rule = [r for r in resp.json()["data"] if r["id"] == rule_id][0]
    assert rule["active_days"] == ["mon", "tue", "wed", "thu", "fri"]
    assert rule["active_hours_start"] == "14:00:00"
    assert rule["active_hours_end"] == "17:00:00"
    assert "2026-03-01" in rule["valid_from"]

    # PATCH scheduling fields.
    resp = client.patch(f"/v1/pos/discount-rules/{rule_id}",
                        json={"active_days": ["sat", "sun"],
                              "active_hours_start": "10:00:00",
                              "active_hours_end": "22:00:00"},
                        headers=admin)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["active_days"] == ["sat", "sun"]
    assert data["active_hours_start"] == "10:00:00"


def test_discount_rejected_when_expired(client, sell_ctx, make_user):
    admin = auth_headers(client, "admin@test.com")
    client.post("/v1/pos/discount-rules", json={
        "code": "OLD", "name": "Expired", "type": "PCT", "value_bp": 1000,
        "valid_from": "2025-01-01T00:00:00Z",
        "valid_to": "2025-12-31T23:59:59Z",
    }, headers=admin)
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]

    resp = client.post(f"/v1/pos/orders/{order['id']}/discounts",
                       json={"code": "OLD"}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "discount_expired"


def test_discount_rejected_when_not_yet_valid(client, sell_ctx, make_user):
    admin = auth_headers(client, "admin@test.com")
    client.post("/v1/pos/discount-rules", json={
        "code": "FUTURE", "name": "Future", "type": "PCT", "value_bp": 1000,
        "valid_from": "2099-01-01T00:00:00Z",
    }, headers=admin)
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]

    resp = client.post(f"/v1/pos/orders/{order['id']}/discounts",
                       json={"code": "FUTURE"}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "discount_not_valid_yet"


def test_discount_rejected_on_wrong_day(client, sell_ctx, make_user):
    admin = auth_headers(client, "admin@test.com")
    # Only valid on a day that is NOT today.
    today_abbr = datetime.now(timezone.utc).strftime("%a").lower()[:3]
    all_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    other_days = [d for d in all_days if d != today_abbr]

    client.post("/v1/pos/discount-rules", json={
        "code": "NOTTODAY", "name": "Not Today", "type": "PCT", "value_bp": 500,
        "active_days": other_days,
    }, headers=admin)
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]

    resp = client.post(f"/v1/pos/orders/{order['id']}/discounts",
                       json={"code": "NOTTODAY"}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "discount_wrong_day"


def test_discount_rejected_outside_active_hours(client, sell_ctx, make_user):
    admin = auth_headers(client, "admin@test.com")
    # Window that is definitely not now: 1 hour window 12 hours from now.
    future_hour = (datetime.now(timezone.utc).hour + 12) % 24
    end_hour = (future_hour + 1) % 24
    client.post("/v1/pos/discount-rules", json={
        "code": "OFFHOURS", "name": "Off Hours", "type": "PCT", "value_bp": 500,
        "active_hours_start": f"{future_hour:02d}:00:00",
        "active_hours_end": f"{end_hour:02d}:00:00",
    }, headers=admin)
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]

    resp = client.post(f"/v1/pos/orders/{order['id']}/discounts",
                       json={"code": "OFFHOURS"}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "discount_outside_hours"


def test_discount_over_limit_needs_a_manager(client, sell_ctx, make_user):
    admin = auth_headers(client, "admin@test.com")
    client.post(
        "/v1/pos/discount-rules",
        json={"code": "STAFF50", "name": "Staff 50%", "type": "PCT",
              "value_bp": 5000, "max_pct_bp": 1000},
        headers=admin,
    )
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]

    denied = client.post(
        f"/v1/pos/orders/{order['id']}/discounts",
        json={"code": "STAFF50"}, headers=headers,
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "discount_needs_approval"

    allowed = client.post(
        f"/v1/pos/orders/{order['id']}/discounts",
        json={"code": "STAFF50"}, headers=_mgr_pos(client, sell_ctx),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["data"]["discount_total_minor"] == 25000  # 50% of 500.00


# ---- POS-3: control --------------------------------------------------------

def test_shift_lifecycle_and_blind_close_variance(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    opened = client.post("/v1/pos/shifts", json={"opening_float_minor": 100000},
                         headers=headers)
    assert opened.status_code == 200, opened.text
    shift_id = opened.json()["data"]["id"]

    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    client.post(f"/v1/pos/orders/{order['id']}/payments",
                json={"method": "CASH", "amount_minor": 50000,
                      "tendered_minor": 50000},
                headers=_idem(headers))

    # X-report mid-shift must NOT reveal expected cash — otherwise the cashier
    # reconciles to the number instead of counting the drawer.
    x = client.get(f"/v1/pos/reports/shift/{shift_id}", headers=headers)
    assert x.status_code == 200
    assert "expected_cash_minor" not in x.json()["data"]
    assert x.json()["data"]["is_final"] is False

    # float 1000.00 + cash 500.00 = 1500.00 expected; declare 1450.00 -> -50.00.
    closed = client.patch(
        f"/v1/pos/shifts/{shift_id}/close",
        json={"declared_cash_minor": 145000}, headers=headers,
    )
    assert closed.status_code == 200, closed.text
    data = closed.json()["data"]
    assert data["expected_cash_minor"] == 150000
    assert data["variance_minor"] == -5000

    z = client.get(f"/v1/pos/reports/shift/{shift_id}", headers=headers).json()["data"]
    assert z["is_final"] is True
    assert z["variance_minor"] == -5000


def test_big_variance_needs_a_manager(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    shift_id = client.post("/v1/pos/shifts", json={"opening_float_minor": 100000},
                           headers=headers).json()["data"]["id"]
    resp = client.patch(
        f"/v1/pos/shifts/{shift_id}/close",
        json={"declared_cash_minor": 0},  # 1000.00 missing
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "variance_needs_approval"


def test_cash_movements_and_no_sale_are_logged(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    shift_id = client.post("/v1/pos/shifts", json={"opening_float_minor": 100000},
                           headers=headers).json()["data"]["id"]
    client.post("/v1/pos/cash-movements",
                json={"type": "DROP", "amount_minor": 20000,
                      "reason_code": "OTHER", "note": "to safe"},
                headers=headers)
    client.post("/v1/pos/cash-movements",
                json={"type": "NO_SALE", "amount_minor": 0,
                      "reason_code": "WRONG_ENTRY"},
                headers=headers)

    report = client.get(f"/v1/pos/reports/shift/{shift_id}", headers=headers)
    types = {m["type"] for m in report.json()["data"]["cash_movements"]}
    assert types == {"DROP", "NO_SALE"}

    # A drop reduces expected cash: 1000.00 float - 200.00 = 800.00.
    closed = client.patch(f"/v1/pos/shifts/{shift_id}/close",
                          json={"declared_cash_minor": 80000}, headers=headers)
    assert closed.json()["data"]["variance_minor"] == 0


def test_order_taker_cannot_open_a_shift(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user,
                           position=BranchPosition.ORDER_TAKER)
    resp = client.post("/v1/pos/shifts", json={"opening_float_minor": 0},
                       headers=headers)
    assert resp.status_code == 403


# ---- POS-4: sync -----------------------------------------------------------

def test_sync_batch_replays_without_duplicating(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    local_id = uuid.uuid4().hex
    env = {"order": {"local_id": local_id,
                     "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]}}

    first = client.post("/v1/pos/sync/batch", json={"envelopes": [env]}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["accepted"] == 1

    # Replaying the same queue must not create a second sale.
    second = client.post("/v1/pos/sync/batch", json={"envelopes": [env]}, headers=headers)
    assert second.json()["data"]["duplicates"] == 1
    assert client.get("/v1/pos/orders", headers=headers).json()["meta"]["total"] == 1


def test_sync_price_drift_flags_rather_than_rejecting(client, sell_ctx, make_user):
    """The device wins for facts, the server for rules: the sale happened, so
    keep it and flag it — never silently re-price, never throw it away."""
    headers = _pos_headers(client, sell_ctx, make_user)
    env = {
        "order": {"local_id": uuid.uuid4().hex,
                  "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]},
        "device_total_minor": 40000,  # priced against a stale menu offline
    }
    resp = client.post("/v1/pos/sync/batch", json={"envelopes": [env]}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["accepted"] == 1
    assert body["flagged"] == 1
    assert body["results"][0]["status"] == "flagged"

    mgr = auth_headers(client, "branch@test.com")
    flagged = client.get("/v1/pos/orders/flagged", headers=mgr)
    assert flagged.status_code == 200, flagged.text
    assert len(flagged.json()["data"]) == 1


def test_sync_one_bad_element_does_not_wedge_the_batch(client, sell_ctx, make_user):
    """A bad element must not block the rest — that is how a device's queue
    never drains."""
    headers = _pos_headers(client, sell_ctx, make_user)
    good = {"order": {"local_id": uuid.uuid4().hex,
                      "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]}}
    bad = {"order": {"local_id": uuid.uuid4().hex,
                     "lines": [{"menu_item_id": 999999, "quantity": 1}]}}
    resp = client.post("/v1/pos/sync/batch",
                       json={"envelopes": [bad, good]}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["accepted"] == 1
    assert body["failed"] == 1
    assert body["results"][1]["status"] == "accepted"


def test_sync_batch_is_capped(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    envs = [
        {"order": {"local_id": uuid.uuid4().hex,
                   "lines": [{"menu_item_id": sell_ctx["burger"], "quantity": 1}]}}
        for _ in range(51)
    ]
    resp = client.post("/v1/pos/sync/batch", json={"envelopes": envs}, headers=headers)
    assert resp.status_code == 422  # schema caps it before the service sees it


# ---- POS-5: recipes now belong to the KITCHEN --------------------------------
# Recipes moved to /v1/kitchen/recipes and drive KITCHEN production, not branch
# sales: the kitchen makes burgers from buns and patties, then allocates finished
# burgers to the branch. The branch never held a bun, so it sells 1:1.
# See tests/test_kitchen_production.py.


# ---- POS-5: Phase 7 feed ---------------------------------------------------

def test_sales_history_feed_is_raw_facts(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"], qty=2).json()["data"]
    client.post(f"/v1/pos/orders/{order['id']}/send", headers=headers)

    mgr = auth_headers(client, "branch@test.com")
    feed = client.get("/v1/pos/feed/sales-history", headers=mgr)
    assert feed.status_code == 200, feed.text
    rows = feed.json()["data"]
    burger = next(r for r in rows
                  if r["product_id"] == sell_ctx["products"]["Burger"].id)
    assert burger["units"] == 2
    assert burger["revenue_minor"] == 100000


def test_planning_shell_admits_phase_7_is_missing(client, sell_ctx):
    """A forecast endpoint returning zeros would look finished. This one says
    plainly that it is waiting on Phase 7."""
    mgr = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/pos/planning", headers=mgr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["ready"] is False
    assert "Phase 7" in data["reason"]
    assert data["forecast"] == []


def test_feed_window_is_capped(client, sell_ctx):
    mgr = auth_headers(client, "branch@test.com")
    resp = client.get(
        "/v1/pos/feed/sales-history?start=2000-01-01&end=2026-01-01", headers=mgr
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "window_too_large"


# ---- POS-6: the abstraction test -------------------------------------------

def test_ae_pack_proves_the_abstraction(client, sell_ctx, make_user, db):
    """Switching a branch to the UAE changes tax with ZERO code changes.

    This is POS-6's real exit criterion: a second country runs with no change to
    any router, the engine, or packs/pk. Only the branch's country_code moves.
    """
    branch = sell_ctx["branch"]
    branch.country_code = "AE"
    branch.province_code = None
    branch.currency = "AED"
    db.flush()

    headers = _pos_headers(client, sell_ctx, make_user)
    boot = client.get("/v1/pos/session/bootstrap", headers=headers)
    assert boot.status_code == 200, boot.text
    pack = boot.json()["data"]["pack"]
    assert pack["version"] == "ae@2026.01"
    assert pack["currency"] == "AED"
    assert pack["is_stub"] is False  # unlike pk, ae states a real position

    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    # 500.00 net + 5% VAT = 525.00
    assert order["subtotal_minor"] == 50000
    assert order["tax_total_minor"] == 2500
    assert order["grand_total_minor"] == 52500
    assert order["pack_version"] == "ae@2026.01"


def test_pk_stub_still_states_no_tax_position(client, sell_ctx, make_user):
    headers = _pos_headers(client, sell_ctx, make_user)
    order = _create(client, headers, sell_ctx["burger"]).json()["data"]
    # The PK pack is a 0% stub pending the tax consultant — it must not quietly
    # invent a rate.
    assert order["tax_total_minor"] == 0
    assert order["pack_version"] == "pk@stub-0pct"
