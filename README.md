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
pytest                    # full suite
```

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

Structure: see `app/` (core, db, models, schemas, deps, services, api/v1).
