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
pytest                    # full suite
```

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
- `POST /v1/admin/sales` — record a sale
- `GET  /v1/admin/sales/records` — list sales (paginated, optional `branch_id`)
- `GET  /v1/admin/sales/summary?period=daily|weekly|monthly` — aggregated totals (optional `start`/`end`/`branch_id`)
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
- `GET  /v1/super-admin/restaurants/{id}/billing` — billing (invoices stubbed until Phase 8)

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

Structure: see `app/` (core, db, models, schemas, deps, services, api/v1).
