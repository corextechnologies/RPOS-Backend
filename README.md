# Restaurant OS — Backend

FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL (Neon).

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |  *nix: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill in DATABASE_URL and TEST_DATABASE_URL
```

## Database

```bash
alembic upgrade head      # create schema
python seed_data.py       # sample restaurant + super admin + admin + branch
```

Seed credentials:
- Super Admin: `admin@test.com` / `Test@1234`
- Admin:       `owner@acme.test` / `Admin@1234`

When `ENV=development` (default), any Admin/manager provisioned via the API also
gets password `Admin@1234` (not a random password). Use a non-dev `ENV` in
production so passwords stay random.

## Run

```bash
uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
```

## Test

```bash
python verify_phase0.py   # Phase 0 gate (needs TEST_DATABASE_URL)
python verify_phase1.py   # Phase 1 gate (+ Phase 0 regression)
python verify_phase6a.py  # Phase 6A gate (+ Phase 0/1 regression)
python verify_phase2.py   # Phase 2 gate (+ Phase 0/1/6A regression)
python verify_phase3.py   # Phase 3 gate (+ Phase 0/1/2/6A + billing smoke)
python verify_phase4.py   # Phase 4 gate (+ Phase 0/1/2/3/6A + billing smoke)
python verify_phase5.py   # Phase 5 gate (+ Phase 0/1/2/3/4/6A/8 regression)
python verify_phase8.py   # Phase 8 gate (+ Phase 0–1 regression)
pytest                    # full suite
```

> Tests build the schema with `create_all`, **not** migrations — a broken
> migration still passes `pytest`. Run `alembic upgrade head` against a scratch
> DB before shipping one.

## Billing cycle job (Phase 8)

Run daily via cron or manually:

```bash
python -m app.jobs.billing_cycle
```

Example cron (02:00 daily):

```
0 2 * * * cd /path/to/RPOS-Backend && .venv/bin/python -m app.jobs.billing_cycle
```

Super Admin can also trigger a run via API: `POST /v1/super-admin/billing/run-cycle`.

### Signup payment & invoice status

On create restaurant with a plan amount, invoices are always seeded:
- **today's** invoice: `paid = payment_received` (`true` if ticked, else `false`)
- **next billing date** invoice: always `paid = false`

Send explicitly:

```json
{ "payment_received": false }
```

or omit the field (defaults to `false` → unpaid today).

If payment comes later:
- `POST /v1/super-admin/restaurants/{id}/billing/record-payment`

Update invoice status (Super Admin):
- `PATCH /v1/super-admin/restaurants/{id}/invoices/{invoice_id}` with `{ "paid": true|false }`
- Marking an invoice **paid** automatically opens the next month’s unpaid invoice.

## Phase 5 endpoints

Branch portal. `BRANCH_MANAGER` unless noted; `BRANCH_STAFF`
(salesperson / cashier / order-taker, via `position`) may take orders and add
customers. Every caller must have a `branch_id`.

- `POST /v1/branch/users` · `GET /v1/branch/users` — create/list branch sub-staff (position-based, `created_by` subtree) + credential email
- `POST /v1/branch/requests` — request product from Admin, **naming the target kitchen** (`kitchen_id`)
- `GET  /v1/branch/requests` · `GET .../{id}` · `PATCH .../{id}/status` — list own / detail / confirm receipt (`ALLOCATED → RECEIVED`)
- `GET  /v1/branch/inventory` · `/inventory/near-expiry` — on-hand + near-expiry (never exposes `cost_price`)
- `POST /v1/branch/stock/waste` — waste·expiry (`WASTE`/`EXPIRY`, optional `waste_reason`)
- `POST /v1/branch/orders` · `GET /v1/branch/orders` — take/list customer orders *(also BRANCH_STAFF)*
- `POST /v1/branch/customers` · `GET /v1/branch/customers` — customer records *(also BRANCH_STAFF)*

The branch sets `target = KITCHEN` at request creation, so the whole
`BRANCH_TO_ADMIN` chain is routed to that kitchen (Admin forwards without
re-selecting). `RECEIVED` credits branch inventory; taking an order deducts
branch inventory per line and rolls the total up into `SalesRecord` so the Admin
sales view reflects real branch sales.

### How the branch flows work

**1 — Branch requests 200 buns from Kitchen A, then receives them**

```
Branch  POST /v1/branch/requests {kitchen_id: A, 200 buns}   → PENDING (target = Kitchen A)
Admin   PATCH → APPROVED  →  FORWARDED_TO_KITCHEN            (no kitchen re-selection needed)
Kitchen A PATCH → IN_PRODUCTION → PRODUCED → ALLOCATED      ⇒ Kitchen A stock −200
Branch  PATCH /v1/branch/requests/{id}/status → RECEIVED    ⇒ branch stock +200
```

**2 — Cashier takes a customer order**

```
Cashier POST /v1/branch/orders {2 burgers @ 10.00}
  ⇒ branch stock −2, order total 20.00
  ⇒ a SalesRecord (20.00) is written → shows up in GET /v1/admin/sales/summary
```
Goes wrong: ordering more than the branch holds → `409 insufficient_stock`, and
the whole order rolls back (no partial deduction, no order, no sales row).

## Phase 4 endpoints

Cloud Kitchen. `KITCHEN_MANAGER` unless noted; `SUB_CHEF` is read + waste only.
Every caller must have a `kitchen_id`.

- `POST /v1/kitchen/users` · `GET /v1/kitchen/users` — create/list sub-chefs (`created_by` subtree) + credential email
- `POST /v1/kitchen/stock/waste` — waste·expiry with a required `waste_reason` *(also SUB_CHEF)*
- `POST /v1/kitchen/stock/counts` · `GET .../counts` — physical count; variance writes an `ADJUSTMENT` and notifies Admin
- `GET  /v1/kitchen/inventory` · `/inventory/near-expiry` — on-hand + near-expiry feed *(also SUB_CHEF)*
- `GET  /v1/kitchen/inventory/labels` — expiry sticker data (`product_id`/`batch_code` filters) *(also SUB_CHEF)*
- `POST /v1/kitchen/requests/warehouse` · `GET .../warehouse` — create/list `KITCHEN_TO_WAREHOUSE` (needs `warehouse_id`)
- `GET  /v1/kitchen/requests/branch` — `BRANCH_TO_ADMIN` inbox, only once forwarded **to this kitchen** *(also SUB_CHEF)*
- `GET  /v1/kitchen/requests/{id}` *(also SUB_CHEF)* · `PATCH .../status` — detail / production + allocation

No kitchen response ever exposes `cost_price` — same rule as Warehouse.

**Contract change:** `PATCH /v1/requests/{id}/status` now accepts
`target_location_type` / `target_location_id`, and **requires** them when Admin
moves a `BRANCH_TO_ADMIN` request to `FORWARDED_TO_KITCHEN`. That target decides
which kitchen sees the request, who is notified, and whose stock `ALLOCATED`
decrements. Forwarding without it returns `409 missing_kitchen_target`.

### How the kitchen flows work

**1 — Kitchen Manager onboards a sub-chef**

```
Manager POST /v1/kitchen/users {"email": "priya@…"}
  → Priya created as SUB_CHEF, kitchen_id = manager's kitchen, created_by_id = manager
  → credential email sent
  → Priya can log waste + read inventory
  → Priya PATCH .../status  →  403 (sub-chefs never approve)
```
Goes wrong: another kitchen's manager calls `GET /v1/kitchen/users` → Priya is
absent. Staff are only visible to the manager who created them.

**2 — Kitchen pulls 50kg flour from the Warehouse**

```
Kitchen  POST /v1/kitchen/requests/warehouse {warehouse_id, 50 flour}   → PENDING
Warehouse PATCH → APPROVED
Warehouse PATCH → DISPATCHED    ⇒ warehouse stock −50   (in transit: kitchen still 0)
Kitchen   PATCH → RECEIVED      ⇒ kitchen stock +50
```
Goes wrong: dispatching more than the warehouse holds → `409 insufficient_stock`,
and the status stays put — no movement without stock.

**3 — Branch asks for 200 buns, Kitchen A produces them**

```
Branch POST /v1/requests (BRANCH_TO_ADMIN, 200 buns)          → PENDING
Admin  PATCH → APPROVED
Admin  PATCH → FORWARDED_TO_KITCHEN + target = Kitchen A
                                    ⇒ only Kitchen A's manager is notified
                                    ⇒ appears in Kitchen A's /requests/branch inbox
Kitchen A PATCH → IN_PRODUCTION     (status only, no stock effect)
Kitchen A PATCH → PRODUCED          (status only, no stock effect)
Kitchen A PATCH → ALLOCATED         ⇒ Kitchen A stock −200
Branch    PATCH → RECEIVED          (branch credit lands in Phase 5)
```
Goes wrong: Kitchen B opening that request → `404`. Allocating 200 when the
kitchen holds 150 → `409 insufficient_stock`.

**4 — Waste, then a nightly count**

```
Priya POST /v1/kitchen/stock/waste {5kg, reason: SPOILAGE}
  ⇒ kitchen stock −5, WASTE movement carries the reason code

Manager POST /v1/kitchen/stock/counts {flour counted: 42}   (system says 45)
  ⇒ variance −3 → ADJUSTMENT movement → inventory corrected to 42
  ⇒ Admin notified: "1 of 1 products differed … (Flour -3)"
```
Goes wrong: counting the same product twice in one submission →
`409 duplicate_count_line`. A count that matches system stock writes no
movement, but Admin is still told the count happened.

## Phase 3 endpoints

Warehouse (all require WAREHOUSE_MANAGER; manager must have `warehouse_id`):
- `POST /v1/warehouse/users` · `GET /v1/warehouse/users` — create/list sub-staff (`created_by` subtree) + credential email
- `POST /v1/warehouse/stock/receive` · `/adjust` · `/waste` — inventory intake / adjustment / waste·expiry
- `GET  /v1/warehouse/inventory` · `/inventory/near-expiry` — on-hand + near-expiry feed (never exposes `cost_price`)
- `POST /v1/warehouse/requests/po` · `GET .../po` — create/list `WAREHOUSE_TO_ADMIN_PO`
- `GET  /v1/warehouse/requests/kitchen` — `KITCHEN_TO_WAREHOUSE` inbox
- `GET  /v1/warehouse/requests/{id}` · `PATCH .../status` — detail / approve·dispatch·mark PO received

`KITCHEN_TO_WAREHOUSE` → `DISPATCHED` decrements warehouse inventory via StockMovement (Phase 6B).

## Phase 2 endpoints

Admin (all require ADMIN role):
- `POST /v1/admin/branches` · `/kitchens` · `/warehouses` — create locations (`branch_limit` enforced on branches)
- `PATCH /v1/admin/branches/{id}` · `/kitchens/{id}` · `/warehouses/{id}` — edit name/location
- `DELETE /v1/admin/branches/{id}` · `/kitchens/{id}` · `/warehouses/{id}` — delete a location
- `POST /v1/admin/users` — create Warehouse/Kitchen/Branch manager + credential email
- `PATCH /v1/admin/users/{id}` — edit a manager (name, location, active flag)
- `POST /v1/admin/users/{id}/revoke` · `/restore` — revoke / restore manager access (soft `is_active`)
- `DELETE /v1/admin/users/{id}` — delete a manager
- `GET  /v1/admin/settings` · `PATCH /v1/admin/settings` — admin's own display name + profile picture URL
- `GET  /v1/admin/sales/records` — list sales (read-only; paginated, optional `branch_id`)
- `GET  /v1/admin/sales/summary?period=daily|weekly|monthly` — aggregated totals, read-only (optional `start`/`end`/`branch_id`)
- `PATCH /v1/admin/products/{id}/pricing` — set `cost_price` (Admin-only field)
- `GET  /v1/admin/products/pricing` — list products with cost prices
- `GET  /v1/admin/requests/products` — `BRANCH_TO_ADMIN` inbox
- `GET  /v1/admin/requests/distribution` — `WAREHOUSE_TO_ADMIN_PO` inbox
- `GET  /v1/admin/requests/{id}` · `PATCH .../status` — admin request detail/actions (delegates to Phase 6A)
- `GET  /v1/admin/employees` — managers in restaurant (excludes the ADMIN owner)
- `GET  /v1/admin/branches` · `/kitchens` · `/warehouses` — list locations
- `GET  /v1/admin/billing` — read-only billing for caller's restaurant

## Phase 6A endpoints

Shared request engine (role-gated per transition):
- `POST /v1/requests` — create a workflow request with line items
- `GET  /v1/requests` — list requests visible to the caller (filters: `request_type`, `status`)
- `GET  /v1/requests/{id}` — request detail with line items
- `PATCH /v1/requests/{id}/status` — single transition endpoint for all workflows

Notifications:
- `GET  /v1/notifications` — inbox for the current user
- `PATCH /v1/notifications/{id}/read` — mark one notification read

## Phase 1 endpoints

Super Admin (all require SUPER_ADMIN):
- `POST /v1/super-admin/restaurants` — create restaurant + first Admin (one txn) + credential email
- `GET  /v1/super-admin/restaurants` / `GET .../{id}` — list / read
- `PATCH /v1/super-admin/restaurants/{id}` — plan tier / branch limit / contact
- `POST /v1/super-admin/restaurants/{id}/halt` · `/activate` — plan status (enforced at auth layer)
- `GET  /v1/super-admin/restaurants/{id}/billing` — billing + invoice history
- `POST /v1/super-admin/restaurants/{id}/billing/record-payment` — seed paid + unpaid invoices
- `PATCH /v1/super-admin/restaurants/{id}/invoices/{invoice_id}` — set invoice `paid` status
- `POST /v1/super-admin/billing/run-cycle` — generate due invoices (manual trigger)
- `GET  /v1/super-admin/income/summary` — platform income + acquisition (`month` or `from_date`/`to_date`)
- `GET  /v1/super-admin/income/forecast?horizon=1|6|12` — restaurants to onboard + collections
- `GET  /v1/super-admin/income/export.csv` — CSV export for the filtered period

Admin:
- `GET /v1/admin/billing` — read-only view of the caller's own restaurant billing

## Phases

- **Phase 0 — Foundation:** schema (Restaurant/User/Branch/Kitchen/Warehouse/
  Product), multi-tenancy `restaurant_id` scoping, single JWT login for all 5
  roles + refresh/logout with DB-backed revocation, RBAC guard, and the central
  hierarchy/visibility engine (`app/deps/scoping.py`).
- **Phase 1 — Super Admin:** add-restaurant (+ first Admin, one transaction),
  plan activate/halt (enforced at the auth layer), edit restaurant/plan, billing
  read endpoints, credential provisioning.
- **Phase 6A — Shared engine:** unified `Request` workflow (4 types), state
  machine transitions, append-only `AuditLog`, notification pipeline on create
  and status change. Stock side effects deferred to Phase 6B.
- **Phase 2 — Admin portal:** location CRUD (branch/kitchen/warehouse),
  manager user provisioning, product `cost_price`, admin request inbox/actions,
  employee and location read APIs. Orders/inventory reads deferred to Phase 3/5.
- **Phase 3 — Warehouse portal:** inventory ledger (`InventoryItem` /
  `StockMovement`), warehouse staff provisioning, stock receive/adjust/waste,
  near-expiry feed, PO + kitchen request wrappers. Dispatching kitchen requests
  decrements warehouse stock (Phase 6B).
- **Phase 4 — Cloud Kitchen portal:** `SUB_CHEF` role (read + waste, scoped by
  `created_by_id`), kitchen waste with a shared `WasteReason` enum (retrofitted
  onto Warehouse), physical `StockCount` that reconciles inventory and notifies
  Admin, shared expiry-label service (`app/services/labels.py`), and the
  warehouse→kitchen request loop. Completes Phase 6B's stock side effects:
  `KITCHEN_TO_WAREHOUSE → RECEIVED` credits the kitchen and
  `BRANCH_TO_ADMIN → ALLOCATED` decrements it. Requests are now routed to a
  specific kitchen, so multi-kitchen restaurants stay isolated.
- **Phase 8 — Billing core:** `Invoice` model, billing cycle engine
  (`app/services/billing.py`), CLI job (`python -m app.jobs.billing_cycle`),
  invoice history on existing billing read APIs. Billing-due notifications and
  plan-change requests deferred until Phase 6.
- **Super Admin Income:** cross-tenant subscription collections + restaurant
  acquisition KPIs, charts series (`by_day` / `by_month`), forecast horizons,
  aging, plan-tier mix, period compare, CSV export (`app/services/income.py`).

Structure: see `app/` (core, db, models, schemas, services, jobs, deps, api/v1).
