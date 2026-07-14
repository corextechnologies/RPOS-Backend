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
python verify_phase8.py   # Phase 8 gate (+ Phase 0–1 regression)
pytest                    # full suite
```

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
- `POST /v1/admin/users` — create Warehouse/Kitchen/Branch manager + credential email
- `PATCH /v1/admin/products/{id}/pricing` — set `cost_price` (Admin-only field)
- `GET  /v1/admin/products/pricing` — list products with cost prices
- `GET  /v1/admin/requests/products` — `BRANCH_TO_ADMIN` inbox
- `GET  /v1/admin/requests/distribution` — `WAREHOUSE_TO_ADMIN_PO` inbox
- `GET  /v1/admin/requests/{id}` · `PATCH .../status` — admin request detail/actions (delegates to Phase 6A)
- `GET  /v1/admin/employees` — all users in restaurant
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
  decrements warehouse stock (Phase 6B). Kitchen receive credit deferred to Phase 4.
- **Phase 8 — Billing core:** `Invoice` model, billing cycle engine
  (`app/services/billing.py`), CLI job (`python -m app.jobs.billing_cycle`),
  invoice history on existing billing read APIs. Billing-due notifications and
  plan-change requests deferred until Phase 6.
- **Super Admin Income:** cross-tenant subscription collections + restaurant
  acquisition KPIs, charts series (`by_day` / `by_month`), forecast horizons,
  aging, plan-tier mix, period compare, CSV export (`app/services/income.py`).

Structure: see `app/` (core, db, models, schemas, services, jobs, deps, api/v1).
