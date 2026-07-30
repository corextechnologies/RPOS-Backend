"""Phase 1: device-facing GET /pos/config snapshot + ETag/If-None-Match.

The device caches this bundle to route and print offline. The version token must
change on any routing-config change (incl. an address edit) but NOT on a printer
runtime status flip — a printer going offline should not force every device to
re-pull its routing.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.enums import BranchPosition, UserRole
from app.models.location import Branch
from app.models.pos import Device
from app.models.printing import Station
from tests.conftest import auth_headers, pair_terminal


@pytest.fixture
def cfg_ctx(client, restaurant_setup, make_user, db):
    """Branch-manager headers + a paired counter terminal with a cashier signed in
    (the device session that reads /pos/config)."""
    mgr = auth_headers(client, "branch@test.com")
    device_uid = pair_terminal(client, mgr, code="T1", profile="COUNTER")
    make_user(
        "cashier@test.com",
        UserRole.BRANCH_STAFF,
        restaurant_id=restaurant_setup["restaurant"].id,
        branch_id=restaurant_setup["home_branch"].id,
        position=BranchPosition.CASHIER,
    )
    login = client.post(
        "/v1/pos/session/login",
        json={"email": "cashier@test.com", "password": "Pass@1234",
              "device_uid": device_uid},
    )
    assert login.status_code == 200, login.text
    pos = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    device = db.execute(
        select(Device).where(
            Device.code == "T1",
            Device.branch_id == restaurant_setup["home_branch"].id,
        )
    ).scalar_one()
    return {"mgr": mgr, "pos": pos, "device_id": device.id, **restaurant_setup}


def _station(client, mgr, code, **extra):
    r = client.post(
        "/v1/branch/printing/stations",
        json={"name": code, "code": code, **extra},
        headers=mgr,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _printer(client, mgr, **body):
    r = client.post("/v1/branch/printing/printers", json=body, headers=mgr)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_config_bundle_and_etag(client, cfg_ctx):
    mgr, pos = cfg_ctx["mgr"], cfg_ctx["pos"]
    grill = _station(client, mgr, "GRILL")
    _printer(client, mgr, role="KITCHEN", connection="LAN",
             address="192.168.1.50:9100", station_id=grill["id"])
    _printer(client, mgr, role="RECEIPT", connection="LAN",
             address="192.168.1.20:9100", device_id=cfg_ctx["device_id"])
    client.put(
        "/v1/branch/printing/category-map",
        json={"category": "Grill", "station_id": grill["id"]},
        headers=mgr,
    )

    resp = client.get("/v1/pos/config", headers=pos)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["config_version"]
    assert len(data["stations"]) == 1
    assert len(data["printers"]) == 2
    assert len(data["category_map"]) == 1
    assert data["item_overrides"] == []
    assert data["payment_accounts"] == []
    assert data["receipt_printer"] is not None
    assert data["receipt_printer"]["address"] == "192.168.1.20:9100"

    etag = resp.headers.get("ETag")
    assert etag == f'"cfg-{cfg_ctx["home_branch"].id}-{data["config_version"]}"'


def test_config_if_none_match_304(client, cfg_ctx):
    mgr, pos = cfg_ctx["mgr"], cfg_ctx["pos"]
    _station(client, mgr, "GRILL")
    first = client.get("/v1/pos/config", headers=pos)
    etag = first.headers["ETag"]

    again = client.get("/v1/pos/config", headers={**pos, "If-None-Match": etag})
    assert again.status_code == 304, again.text
    assert again.headers.get("ETag") == etag
    assert again.text == ""


def test_config_version_changes_on_address_edit(client, cfg_ctx):
    mgr, pos = cfg_ctx["mgr"], cfg_ctx["pos"]
    grill = _station(client, mgr, "GRILL")
    p = _printer(client, mgr, role="KITCHEN", connection="LAN",
                 address="192.168.1.50:9100", station_id=grill["id"])

    r1 = client.get("/v1/pos/config", headers=pos)
    v1 = r1.json()["data"]["config_version"]
    etag1 = r1.headers["ETag"]

    upd = client.patch(
        f"/v1/branch/printing/printers/{p['id']}",
        json={"address": "10.0.0.9:9100"},
        headers=mgr,
    )
    assert upd.status_code == 200, upd.text

    # The old ETag must no longer match -> full 200 with a new version.
    r2 = client.get("/v1/pos/config", headers={**pos, "If-None-Match": etag1})
    assert r2.status_code == 200, r2.text
    data = r2.json()["data"]
    assert data["config_version"] != v1
    assert data["printers"][0]["address"] == "10.0.0.9:9100"


def test_status_flip_does_not_bump_version(client, cfg_ctx):
    """A runtime status change must not invalidate every device's cached routing."""
    mgr, pos = cfg_ctx["mgr"], cfg_ctx["pos"]
    grill = _station(client, mgr, "GRILL")
    p = _printer(client, mgr, role="KITCHEN", connection="LAN",
                 address="192.168.1.50:9100", station_id=grill["id"])
    etag1 = client.get("/v1/pos/config", headers=pos).headers["ETag"]

    upd = client.patch(
        f"/v1/branch/printing/printers/{p['id']}",
        json={"status": "OFFLINE"},
        headers=mgr,
    )
    assert upd.status_code == 200, upd.text

    again = client.get("/v1/pos/config", headers={**pos, "If-None-Match": etag1})
    assert again.status_code == 304, again.text


def test_receipt_printer_null_when_not_bound(client, cfg_ctx):
    mgr, pos = cfg_ctx["mgr"], cfg_ctx["pos"]
    # A branch RECEIPT printer that is not bound to this device.
    _printer(client, mgr, role="RECEIPT", connection="LAN", address="192.168.1.99:9100")
    data = client.get("/v1/pos/config", headers=pos).json()["data"]
    assert data["receipt_printer"] is None


def test_config_branch_isolation(client, cfg_ctx, db):
    mgr, pos = cfg_ctx["mgr"], cfg_ctx["pos"]
    _station(client, mgr, "GRILL")  # this branch

    # A second branch with its own station must not leak into this device's config.
    b2 = Branch(restaurant_id=cfg_ctx["restaurant"].id, name="B2", location="L2")
    db.add(b2)
    db.flush()
    db.add(Station(
        restaurant_id=cfg_ctx["restaurant"].id, branch_id=b2.id,
        name="Other", code="OTHER",
    ))
    db.flush()

    data = client.get("/v1/pos/config", headers=pos).json()["data"]
    assert {s["code"] for s in data["stations"]} == {"GRILL"}
