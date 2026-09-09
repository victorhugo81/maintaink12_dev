# Phase 12 Report — Mobile Optimization & Security Audit

Status: **complete** (final phase). Definition of done met — mobile pass on the technician
flows, full security review with an automatic field-level audit log in place, full
regression suite green (509/509, 74 new), and the final migration/changelog summary
delivered as `docs/MIGRATION_GUIDE.md`.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_11_REPORT.md`, `MIGRATION_GUIDE.md`, `../application/CLAUDE.md`.

## 1. Mobile-responsive pass

Scope was the six technician flows the phase names. Method: a template-level review (no
real device — see the caveat), plus a rendered check of the mobile menu as a Technician.

| Flow | Where it lives | Finding / change |
|---|---|---|
| Today's-work view | M&O Dashboard, Technician view (Phase 9) | "My Open Work Orders" already in `table-responsive`; KPI cards use `col-sm-6` |
| Work order detail/update | `edit_work_order.html` | Three tables (labor, materials, status history) had no responsive wrapper — **wrapped**. Status-change form is `w-100`. Sidebar collapses to `col-lg-3` → full width under `lg` |
| Photo upload | request form, WO detail, asset condition | Already `accept="image/jpeg,image/png"`; the two camera-first flows (request photo, condition photo) already carry `capture="environment"` from Phases 2–3. Left as-is: `capture` on the document-style inputs would hide the gallery picker on Android |
| QR-code landing | `edit_asset.html` (the QR encodes its URL) | Condition-history table had no wrapper — **wrapped** |
| Labor/parts recording | `edit_work_order.html` sections | Inputs are `form-control` in `col-12 col-sm-*` rows; one `col-auto` with `min-width: 220px` in the cost form is under the narrowest viewport (320px) so left alone |
| Checklist completion | `edit_inspection.html` | Results table had no wrapper — **wrapped**; the entry form is one full-width row per item |

"Technicians never need the desktop nav": rendered the mobile menu as a Technician —
M&O Dashboard, Work Orders, New Request, PM Dashboard, Inspections, Facilities, Rooms,
Assets, Search, Profile, Notification Preferences are all present. Every table on the two
dashboard panels that lacked a wrapper (open-issues-by-facility, both views) was wrapped
too. `base.html` already sets the viewport meta.

**Caveat:** this is a markup pass verified by rendering, not a device/emulator pass — no
browser was driven at any point in this project. Listed as the top follow-up.

## 2. Security review

Method: a script enumerated all 101 M&O routes from `routes.py` and flagged any without
`@login_required` or a recognized guard (`is_admin`, `is_staff`, `can_access_site`,
`can_access_work_order`, `can_manage_work_order`, `can_access_inspection`,
`_visible_site_ids`, ownership check) — zero flagged. Then a manual read of every flagged
category below, then tests for each claim.

| Area | Finding | Evidence |
|---|---|---|
| Auth | Every M&O route is `@login_required` | script + `TestAuthRequired` (pre-existing) |
| Authorization | Admin-only reference data/CRUD; staff-only views; site-scoped detail routes; sub-resource routes (labor/material/attachment/task/cost/document deletes) derive the parent from the row, never a request param | manual read; `TestPrivilegeEscalation` (17 admin routes × Technician, 9 staff routes × School Staff) |
| IDOR | A site-2 Technician gets 403 on site-1 work order, its attachment, asset, QR, asset attachment, facility, facility attachment, room, inspection, project document (GET) and on status change, comment, labor, condition, inspection results (POST); list pages hide other sites' rows; School Staff can't open another user's work order | `TestIdorOtherSiteTechnician`, `TestIdorRegularUser` |
| CSRF | `CSRFProtect` is global; every state-changing form carries a token; a token-less POST is 400 and writes nothing | `TestCsrf` (toggles `WTF_CSRF_ENABLED` in a try/finally); live-server curl (400) |
| SQL injection | ORM throughout; search uses bound `ILIKE` params; `report_key`/`entity_type`/`dimension` validated against registries; the one legacy `sort` param is a string compare | grep, manual read |
| XSS | Jinja autoescape; no `|safe` or `Markup()` anywhere in M&O templates/routes; CSP with per-request nonce (pre-existing) | grep; `TestSessionAndHeaders` |
| File upload | `validate_file_upload()` — extension AND magic-byte check, size cap; stored under a generated `secure_filename()` name, never the client's | `TestUploadValidation` (php rejected, spoofed png rejected, `../../../etc/evil.png` stored as a clean generated name) |
| Path traversal | Download routes pass a DB-stored generated name to `send_from_directory`; a tampered `../secret.txt` name 404s | `TestPathTraversal` |
| Session | Production config: `SESSION_COOKIE_SECURE/HTTPONLY`, `SameSite=Lax`, 8h lifetime, 16MB `MAX_CONTENT_LENGTH` (all pre-existing, now asserted) | `TestSessionAndHeaders` |
| Rate limiting | Login/reset limited (pre-existing); **added** `60/min` on `/search` and `10/min` on the CSV upload | `TestSessionAndHeaders` |
| Privilege escalation | `role_id` posted to `/profile` is ignored; role changes are admin-only and now audited | `TestPrivilegeEscalation` |
| Legacy regression | `/`, tickets, users, sites, roles, titles, notifications, organization, profile, bulk upload all still load | `TestLegacyRegression` |

Two things worth knowing that were **not** changed: `/users` genuinely allows
`is_tech_role` (AssistItK12 design), so the "Users" link Technicians see is a real page,
not a broken one; and `organization.html` reads `email_form.csrf_token._value()` inline, so
it can only render with CSRF enabled — true in every real config, off in the test suite,
which is why its regression test toggles CSRF on rather than touching the legacy template.

## 3. Audit log

`AuditLog` + `application/audit.py`: one row per changed **field** — entity type/id/label,
action (create/update/delete), field, old value, new value, acting user (NULL = "System"
for the PM generator, notification sweep, CSV importer), timestamp. Tracked: WorkOrder,
Asset, Facility, Room, User, Role, CostRecord, WorkOrderLabor, WorkOrderMaterial, Project,
ProjectCost, Vendor, SLARule, AssetConditionHistory — the phase's list (work orders,
assets, facilities, users, permissions, costs, status changes) plus the obvious neighbours.

Implemented as two SQLAlchemy `Session` flush listeners rather than per-route calls, so
every path that saves a tracked model is covered and a future route can't forget. Status
changes are just `WorkOrder.status` changing, so they're logged like any field (alongside
the existing `WorkOrderStatusHistory`). Password/email/lockout columns are logged as
changed with both values masked. Admin-only read-only page at `/audit_log` with entity
type/id/user filters and pagination.

### Two bugs caught by the first smoke run

1. **Password resets were silently dropped.** Masking replaced old and new with `***`
   *before* the "skip if old == new" no-op check, so every masked change compared equal
   and was discarded. Fixed by comparing raw values first, masking after.
2. **A second edit after a commit logged `old_value=None`.** After `commit()` expires an
   instance, a scalar's attribute history has no `deleted` side — and
   `AttributeState.load_history()` does not load it for scalar columns despite its
   docstring. Fixed by reading the committed value with a Core `SELECT` on the flush's own
   connection (Core → no autoflush → no recursion into the listener). Tested explicitly.

## What was tested

`tests/test_audit_and_security.py` — 74 tests: audit capture (create attributed to the
user, one row per changed field with old/new, unchanged fields produce no row, status
change via `workflow.apply_transition` attributed to System, second-edit-after-commit keeps
the old value, role change + masked password, cost record create-then-update, delete,
admin-only page with filters); the IDOR/privilege/CSRF/upload/traversal/session/rate-limit
claims above; and the legacy regression sweep. Full suite 509 passing; reordered runs in
both directions clean.

Live server: logged in, edited a facility with a real CSRF token (302), repeated the POST
without a token (400, nothing written), and confirmed the `year_built` change on
`/audit_log` attributed to the admin. Server log clean.

## Deviations from the plan (and why)

1. **Mobile pass is markup-and-render verified, not device verified** — no browser or
   emulator was available in any phase; every responsive claim above is about Bootstrap
   classes and wrappers confirmed in rendered HTML.
2. **Audit log via ORM listener, not explicit calls** — the phase asks for coverage of
   several entity types; a listener guarantees it for every save path, and it's the same
   "one place, can't be forgotten" reasoning as `refresh_cost_record()` (Phase 7) and
   `workflow.apply_transition()` (Phase 3).
3. **`capture="environment"` not added to document-style uploads** — it would remove the
   gallery/file picker on Android; kept only on the two flows where the camera is the point.
4. **No legacy code changed by the security review** — the two legacy quirks found are
   documented, not patched, per the standing "preserve AssistItK12" principle.

## Known follow-ups (not blockers)

- A real device/emulator pass on the six technician flows.
- `AuditLog` grows one row per field change forever — fine for years at district scale,
  but a retention/archival job is a natural addition.
- Audit `MaintenanceSchedule` / `Inspection` too if inspection-result edits ever become
  possible (today an Inspection is completed exactly once and never edited).
- Phase 12's work (audit log, tests, migration `6d752033cb01`, this report and
  `MIGRATION_GUIDE.md`) is in the working tree; Phases 1–11 were committed by the user as
  `d693592`.
