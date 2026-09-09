# Maintaink12 — Final Summary & Migration Guide for AssistItK12 Installations

Maintaink12 0.12.0 is AssistItK12 plus a complete K–12 Maintenance & Operations CMMS built
alongside it over twelve phases (`docs/PHASE_0_ARCHITECTURE_ANALYSIS.md` through
`docs/PHASE_12_REPORT.md`, one per phase; `CHANGELOG.md` for the version-by-version view).
This document is the single-page answer to: what changed, what was added, what was
preserved, what the schema looks like now, what's new to install, what got more secure, and
how to move an existing AssistItK12 installation over.

## What was preserved (and deliberately not touched)

Everything AssistItK12 already did still works exactly as it did — the same routes, the same
templates, the same behaviour. Specifically untouched:

- The ticketing engine: `Ticket`, `Title`, `Ticket_content`, `Ticket_attachment`, every
  `/tickets*` route, the ticket dashboard at `/`, ticket email notifications.
- Users, Roles, Sites, Organization (incl. SMTP + FTP settings), Notifications (site
  banners), Profile, the legacy Users/Sites bulk upload and its FTP schedule.
- Auth: login, lockout, temporary passwords, must-change-password, email encryption at rest.
- Security posture: CSRF, CSP with per-request nonce, security headers, rate limiting on
  login/reset, session cookie hardening, `validate_file_upload()`.
- The role model: `role_id` 1 Admin, 2 Specialist, 3 Technician, 4+ everyone else. No new
  roles were added — every M&O permission is expressed in terms of these four.

Two pre-existing quirks were found during the final review and **left alone** on purpose
(`PHASE_12_REPORT.md` §2): `/users` is reachable by Technicians/Specialists by design, and
`organization.html` needs CSRF enabled to render (it always is outside the test suite).

## What was added

| Phase | Feature | Key modules |
|---|---|---|
| 1 | Facilities → Floors → Rooms, site-scoped, soft-delete, attachments | `models.py`, `routes.py` |
| 2 | Asset Types, Assets, append-only condition history, QR codes | `risk.py` (later), `qrcode` |
| 3 | Work Orders (separate from Tickets) with an 11-state workflow, priorities/categories, requester + staff forms | `workflow.py`, `reference_data.py` |
| 4 | Preventive maintenance plans/schedules, daily PM work-order generation | `pm.py`, `scheduled_jobs.py` |
| 5 | Inspection templates, scheduled inspections, failed-item work orders | `inspections.py` |
| 6 | Vendors, vendor performance | `vendors.py` |
| 7 | Labor, materials, cost records, cost rollups, technician workload | `costs.py` |
| 8 | Capital projects, Asset Risk Score, Capital Replacement | `projects.py`, `risk.py` |
| 9 | Role-based M&O Dashboard, Facility Health Score, recurring-issue detection, insights, charts | `analytics.py` |
| 10 | Thirteen CSV-exportable reports | `reports.py` |
| 11 | SLA rules + breach/warning, 8-category notification sweep with preferences, global search, CSV import for five entity types | `sla.py`, `notifications.py`, `search.py`, `csv_import.py` |
| 12 | Field-level audit log, security review, mobile pass | `audit.py` |

Design rules that hold across all of it (see `application/CLAUDE.md` for the full list):
every computed figure (risk, health, KPIs, SLA state) shows its inputs and never fabricates a
value for missing data; every scored/aggregated feature lives in a plain, directly-tested
module; site scoping mirrors AssistItK12's; every migration is additive.

## Schema changes

All 11 M&O migrations are **additive** — new tables, nullable columns on existing tables,
and indexes. No AssistItK12 table or column was renamed, dropped, or made stricter.

| Revision | Adds |
|---|---|
| `7d40fabd6b8b` | `facility`, `floor`, `room`, `facility_attachment`, `room_attachment` |
| `f11aade72780` | `asset_type`, `asset`, `asset_condition_history`, `asset_attachment` |
| `cb141050cfff` | `priority`, `category`, `subcategory`, `work_order`, `work_order_comment`, `work_order_attachment`, `work_order_status_history` |
| `bc1779acd4f1` | `maintenance_plan`, `maintenance_schedule` |
| `e7efb8ead9da` | `inspection_template`, `inspection_item`, `inspection`, `inspection_result` |
| `a370d33e0db2` | `vendor`; **column** `work_order.vendor_id` |
| `18f317e4f1a5` | `work_order_labor`, `work_order_material`, `cost_record` |
| `cf427a9e2830` | `project`, `project_task`, `project_cost`, `project_document`, `project_vendor`; **columns** `asset.project_id`, `asset.safety_impact`, `asset.operational_importance`, `work_order.project_id` |
| `d919f88115eb` | index `work_order.completed_at` |
| `c419b2165ce3` | `sla_rule`, `notification_preference`, `notification_log`, `csv_import_log` |
| `6d752033cb01` | `audit_log` |

The chain starts from AssistItK12's own head, `63cb377b163b` (encrypt user email). Existing
tables touched: `work_order` and `asset` only (both M&O tables) — no AssistItK12 table was
altered.

**MySQL vs SQLite note.** Two migrations (`a370d33e0db2`, `cf427a9e2830`) add a foreign-key
column to an existing table. On MySQL (the production target) the FK constraint is created
normally. On SQLite the constraint is skipped (the column and the ORM relationship are
still there) because SQLite's table-recreate path requires named constraints this schema
doesn't have. Nothing to do on MySQL; documented in `application/CLAUDE.md`.

## New dependencies

Runtime: **`qrcode[pil]`** (asset QR codes, Phase 2). That's the only addition — charts use
the Chart.js bundle AssistItK12 already ships, notifications use Flask-Mail, scheduling uses
Flask-APScheduler, all pre-existing. Dev: unchanged (`pytest`, `pytest-flask`, `pip-audit`).

## Security improvements over AssistItK12

- Every one of the 101 M&O routes is `@login_required` with an explicit authorization
  guard; detail/download routes are site-scoped; sub-resource routes derive the parent from
  the row, never a request parameter. Verified by an automated route scan and 74 tests
  (IDOR across every new resource type, privilege escalation, CSRF, uploads, traversal).
- **Audit log** (`/audit_log`): who changed what, old value, new value, when — one row per
  changed field across work orders, assets, facilities, rooms, users, roles, costs, labor,
  materials, projects, vendors, SLA rules — captured automatically at the ORM layer, so no
  save path can bypass it. Sensitive fields are masked.
- Rate limits on the two new open-ended endpoints (`/search`, CSV upload).
- Uploads on every new attachment type reuse `validate_file_upload()` (extension + magic
  bytes) and are stored under generated names; every download goes through
  `send_from_directory` with a DB-stored name.
- CSV import validates every row before writing anything and commits one transaction per
  file, so a mid-write failure can't leave a half-imported file.
- Append-only guarantees: asset condition history is ORM-enforced immutable; audit log is
  read-only by convention (no route writes or deletes it).

## Migration procedure — existing AssistItK12 installation

Assumes a running AssistItK12 on MySQL at Alembic head `63cb377b163b`, deployed the way
`README.md` describes (uv, `.env`, `flask --app main.py`).

1. **Back up the database** (`mysqldump`) and the `application/static/uploads/` tree.
2. **Deploy the Maintaink12 code** over the AssistItK12 checkout (same layout — it is the
   same application). Keep your existing `.env`; no key was renamed. Run `uv sync` to pick
   up `qrcode[pil]`.
3. **Check `.env` for production settings** that were already recommended and are now
   load-bearing for more features: `RATELIMIT_STORAGE_URI=redis://...` (the app warns if it's
   left on `memory://`), `SESSION_COOKIE_SECURE` is forced on in the production config,
   `MAIL_*` if you want the new notification emails (they no-op silently without SMTP, same
   as ticket emails do today).
4. **Run the migrations:** `uv run flask --app main.py db upgrade`. Eleven additive
   revisions apply in order from `63cb377b163b` to `6d752033cb01`. No data migration runs;
   existing rows are untouched.
5. **Seed the M&O reference data** (priorities and categories from `PROJECT_PLAN.md`) —
   idempotent, safe to re-run:
   `uv run python -c "from main import create_app, db; from application.reference_data import seed_reference_data; app=create_app('production'); app.app_context().push(); print(seed_reference_data(db))"`
   (A fresh install gets this from `installation/seed_data.py` automatically.)
6. **Restart the app.** On start it creates the new upload folders under
   `application/static/uploads/` and registers two daily APScheduler jobs:
   `pm_schedule_generation` (01:00) and `notification_sweep` (02:00). Nothing to configure.
7. **Load your inventory** with Admin → CSV Import: Facilities first, then Rooms, then
   Assets (each references the previous), Vendors and any additional Users at any point.
   Download each type's template from the same page; the import reports every row as
   imported / duplicate / error and never partially writes on failure.
8. **Optional configuration** (all admin pages, all safe to leave for later): SLA Rules
   (per-priority response/resolution targets — until one exists, SLA compliance falls back
   to due-date based), Maintenance Plans, Inspection Templates, Notification Preferences
   (per user; everything defaults to on).
9. **Verify:** log in, open the M&O Dashboard, `/reports`, and `/audit_log` (the migration
   itself leaves the audit log empty; your first edit will appear there).

Rollback: `db downgrade 63cb377b163b` removes every M&O table and column (the additive
migrations all have downgrades) and leaves AssistItK12 data exactly as it was — but any M&O
data entered after the upgrade is lost, so restore from step 1's backup only if you also want
to discard that.

## Test suite

509 tests across 17 files, one per phase plus the original AssistItK12 suite — every phase's
tests still run and pass against the final code. `uv run pytest -q` runs everything against
an in-memory SQLite database in about 12 seconds. The suite's conventions (one identity per
test, look rows up by name not id, reorder-check anything that creates global state) are in
`application/CLAUDE.md`.
