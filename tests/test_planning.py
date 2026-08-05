"""Phase 7, Stage 5 — the plan: accept, override, confirm, distribute.

The rule under test throughout: a forecast never acts on its own. It reaches a
kitchen or a branch only after an Admin has confirmed it, and what reaches them
is read-only.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.analytics import DailyProductSales
from app.models.notification import Notification
from app.models.plan import ForecastPlan, ForecastPlanStatus
from app.services.planning import PlanningService
from tests.conftest import auth_headers

TODAY = date.today()
START = TODAY
END = TODAY + timedelta(days=2)


@pytest.fixture
def plan_ctx(db, restaurant_setup, make_product):
    r = restaurant_setup["restaurant"]
    branch = restaurant_setup["home_branch"]
    samosa = make_product(r.id, name="Samosa", sku="SAM")
    chai = make_product(r.id, name="Chai", sku="CHA")
    db.flush()
    # Enough history that both products are forecastable.
    for product, per_day in ((samosa, 20), (chai, 10)):
        for i in range(30):
            db.add(
                DailyProductSales(
                    restaurant_id=r.id,
                    branch_id=branch.id,
                    product_id=product.id,
                    business_date=TODAY - timedelta(days=30 - i),
                    units=per_day,
                    revenue_minor=per_day * 100,
                    order_count=per_day,
                )
            )
    db.flush()
    return {**restaurant_setup, "branch": branch, "samosa": samosa, "chai": chai}


def _draft(db, ctx, start=START, end=END):
    return PlanningService.create_draft(
        db, actor=ctx["admin"], branch_id=ctx["branch"].id,
        start=start, end=end, commit=False,
    )


# --- creating a draft -------------------------------------------------------

def test_a_draft_starts_with_the_suggestion_accepted(db, plan_ctx):
    """Accepting is the default and overriding is the exception — the Admin
    should have to disagree, not have to agree."""
    plan = _draft(db, plan_ctx)
    assert plan.status is ForecastPlanStatus.DRAFT
    assert plan.lines
    for line in plan.lines:
        assert line.planned_units == line.suggested_units
        assert line.is_overridden is False


def test_a_draft_covers_every_day_of_the_window(db, plan_ctx):
    plan = _draft(db, plan_ctx)
    assert {line.on_date for line in plan.lines} == {
        START, START + timedelta(days=1), END
    }


def test_the_breakdown_is_snapshotted_onto_the_line(db, plan_ctx):
    """Sales history and the calendar both keep moving, so recomputing "why did
    we plan this?" later would not reproduce what the Admin actually saw."""
    plan = _draft(db, plan_ctx)
    line = plan.lines[0]
    assert line.baseline is not None
    assert line.weekday_applied is not None
    assert line.event_multiplier is not None
    assert line.maturity


def test_planning_a_branch_with_nothing_to_forecast_is_refused(
    db, plan_ctx, make_branch
):
    """An empty plan would be a confirmed instruction to make nothing."""
    from app.core.exceptions import ConflictError

    empty = make_branch(plan_ctx["restaurant"].id, name="Brand New")
    with pytest.raises(ConflictError):
        PlanningService.create_draft(
            db, actor=plan_ctx["admin"], branch_id=empty.id,
            start=START, end=END, commit=False,
        )


# --- overriding -------------------------------------------------------------

def test_the_admin_can_override_a_line_and_the_suggestion_survives(db, plan_ctx):
    """Keeping the original is what later answers "is the forecast always low?"
    — a plan overridden upward every week is a forecast tuned wrong."""
    plan = _draft(db, plan_ctx)
    line = plan.lines[0]
    original = line.suggested_units

    plan = PlanningService.override_lines(
        db, actor=plan_ctx["admin"], plan_id=plan.id,
        entries=[{"line_id": line.id, "planned_units": original + 25,
                  "reason": "Cricket final, expecting a rush"}],
        commit=False,
    )
    edited = next(l for l in plan.lines if l.id == line.id)
    assert edited.planned_units == original + 25
    assert edited.suggested_units == original      # untouched
    assert edited.is_overridden is True
    assert "Cricket" in edited.override_reason


def test_a_confirmed_plan_cannot_be_edited(db, plan_ctx):
    """The kitchen may already have acted on it — it is replaced, not moved
    underneath them."""
    from app.core.exceptions import ConflictError

    plan = _draft(db, plan_ctx)
    line_id = plan.lines[0].id
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    with pytest.raises(ConflictError):
        PlanningService.override_lines(
            db, actor=plan_ctx["admin"], plan_id=plan.id,
            entries=[{"line_id": line_id, "planned_units": 1}], commit=False,
        )


# --- confirming -------------------------------------------------------------

def test_confirming_records_who_and_when(db, plan_ctx):
    plan = _draft(db, plan_ctx)
    plan = PlanningService.confirm(
        db, actor=plan_ctx["admin"], plan_id=plan.id, commit=False
    )
    assert plan.status is ForecastPlanStatus.CONFIRMED
    assert plan.confirmed_by_id == plan_ctx["admin"].id
    assert plan.confirmed_at is not None


def test_confirming_twice_is_refused(db, plan_ctx):
    from app.core.exceptions import ConflictError

    plan = _draft(db, plan_ctx)
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    with pytest.raises(ConflictError):
        PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                                commit=False)


def test_confirming_notifies_the_branch_and_the_kitchen(db, plan_ctx):
    """Through the same pipeline every other hand-off uses — no separate
    channel just for forecasting."""
    plan = _draft(db, plan_ctx)
    before = db.query(Notification).count()
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    sent = (
        db.query(Notification)
        .filter(Notification.entity_type == "forecast_plan")
        .all()
    )
    assert db.query(Notification).count() > before
    recipients = {n.user_id for n in sent}
    assert plan_ctx["branch_mgr"].id in recipients
    assert plan_ctx["kitchen_mgr"].id in recipients


# --- what Branch and Kitchen see -------------------------------------------

def test_a_branch_sees_nothing_until_the_plan_is_confirmed(db, plan_ctx):
    """A draft is the Admin thinking aloud. Showing it would turn an unfinished
    idea into an instruction."""
    _draft(db, plan_ctx)
    rows = PlanningService.branch_expected_stock(
        db, restaurant_id=plan_ctx["restaurant"].id,
        branch_id=plan_ctx["branch"].id, start=START, end=END,
    )
    assert rows == []


def test_a_branch_sees_its_expected_stock_once_confirmed(db, plan_ctx):
    plan = _draft(db, plan_ctx)
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    rows = PlanningService.branch_expected_stock(
        db, restaurant_id=plan_ctx["restaurant"].id,
        branch_id=plan_ctx["branch"].id, start=START, end=END,
    )
    assert rows
    assert {"date", "product_id", "product_name", "expected_units"} <= set(rows[0])


def test_the_branch_sees_the_overridden_number_not_the_suggestion(db, plan_ctx):
    """What ships is what the Admin decided."""
    plan = _draft(db, plan_ctx)
    line = plan.lines[0]
    PlanningService.override_lines(
        db, actor=plan_ctx["admin"], plan_id=plan.id,
        entries=[{"line_id": line.id, "planned_units": 999}], commit=False,
    )
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    rows = PlanningService.branch_expected_stock(
        db, restaurant_id=plan_ctx["restaurant"].id,
        branch_id=plan_ctx["branch"].id, start=START, end=END,
    )
    assert any(r["expected_units"] == 999 for r in rows)


def test_the_kitchen_sees_totals_summed_across_branches(db, plan_ctx, make_branch):
    """A central kitchen makes one batch for the chain — a per-branch split is
    detail it cannot act on differently."""
    second = make_branch(plan_ctx["restaurant"].id, name="Second Branch")
    for i in range(30):
        db.add(
            DailyProductSales(
                restaurant_id=plan_ctx["restaurant"].id,
                branch_id=second.id,
                product_id=plan_ctx["samosa"].id,
                business_date=TODAY - timedelta(days=30 - i),
                units=5, revenue_minor=500, order_count=5,
            )
        )
    db.flush()

    for branch_id in (plan_ctx["branch"].id, second.id):
        plan = PlanningService.create_draft(
            db, actor=plan_ctx["admin"], branch_id=branch_id,
            start=START, end=START, commit=False,
        )
        PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                                commit=False)

    rows = PlanningService.kitchen_production_targets(
        db, restaurant_id=plan_ctx["restaurant"].id, start=START, end=START
    )
    samosa = next(r for r in rows if r["product_id"] == plan_ctx["samosa"].id)
    assert samosa["branches"] == 2
    assert samosa["target_units"] == 25   # 20 at one branch + 5 at the other


def test_a_cancelled_plan_stops_being_visible(db, plan_ctx):
    plan = _draft(db, plan_ctx)
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    PlanningService.cancel(db, actor=plan_ctx["admin"], plan_id=plan.id,
                           commit=False)
    rows = PlanningService.branch_expected_stock(
        db, restaurant_id=plan_ctx["restaurant"].id,
        branch_id=plan_ctx["branch"].id, start=START, end=END,
    )
    assert rows == []
    # Kept rather than deleted — it was a real decision.
    assert db.get(ForecastPlan, plan.id) is not None


# --- the API surfaces -------------------------------------------------------

def test_admin_runs_the_whole_flow_over_http(client, plan_ctx, db):
    db.commit()
    admin = auth_headers(client, "admin@test.com")

    created = client.post(
        "/v1/admin/plans",
        json={"branch_id": plan_ctx["branch"].id,
              "start": START.isoformat(), "end": END.isoformat()},
        headers=admin,
    )
    assert created.status_code == 200, created.text
    plan = created.json()["data"]
    assert plan["status"] == "DRAFT"
    assert plan["overridden_lines"] == 0

    line = plan["lines"][0]
    patched = client.patch(
        f"/v1/admin/plans/{plan['id']}/lines",
        json={"lines": [{"line_id": line["id"], "planned_units": 55,
                         "reason": "Match day"}]},
        headers=admin,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["overridden_lines"] == 1

    confirmed = client.post(
        f"/v1/admin/plans/{plan['id']}/confirm", headers=admin
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "CONFIRMED"


def test_the_branch_planning_read_fills_in_once_confirmed(client, plan_ctx, db):
    plan = _draft(db, plan_ctx)
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    db.commit()

    mgr = auth_headers(client, "branch@test.com")
    resp = client.get("/v1/pos/planning", headers=mgr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["ready"] is True
    assert data["expected_stock"]


def test_the_kitchen_planning_read_shows_targets(client, plan_ctx, db):
    plan = _draft(db, plan_ctx)
    PlanningService.confirm(db, actor=plan_ctx["admin"], plan_id=plan.id,
                            commit=False)
    db.commit()

    kitchen = auth_headers(client, "kitchen@test.com")
    resp = client.get("/v1/kitchen/planning", headers=kitchen)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["ready"] is True
    assert data["targets"]


def test_plans_are_admin_only_to_write(client, plan_ctx, db):
    """Kitchen and Branch read a plan; only the Admin makes one."""
    db.commit()
    for email in ("branch@test.com", "kitchen@test.com"):
        headers = auth_headers(client, email)
        assert client.post(
            "/v1/admin/plans",
            json={"branch_id": plan_ctx["branch"].id,
                  "start": START.isoformat()},
            headers=headers,
        ).status_code == 403
        assert client.get("/v1/admin/plans", headers=headers).status_code == 403


def test_another_tenants_plan_is_not_reachable(client, plan_ctx, db,
                                               make_restaurant, make_user):
    plan = _draft(db, plan_ctx)
    db.commit()
    other = make_restaurant("Other")
    make_user("otheradmin@test.com", plan_ctx["admin"].role,
              restaurant_id=other.id)
    headers = auth_headers(client, "otheradmin@test.com")
    assert client.get(
        f"/v1/admin/plans/{plan.id}", headers=headers
    ).status_code == 404
