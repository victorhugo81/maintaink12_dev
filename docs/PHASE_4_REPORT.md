# Phase 4 Report — Preventive Maintenance

Status: **complete**. Definition of done met — PM plans/schedules, automatic work-order
generation with a proven no-duplicate guarantee and correct overdue catch-up, and the four
PM dashboard buckets; 17 new tests, 172/172 total, no regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_3_REPORT.md`, `../application/CLAUDE.md`.

## What was built

### Models (`application/models.py`)

| Model | Notes |
|---|---|
| `MaintenancePlan` | tied to **exactly one** of a specific `Asset` or an `AssetType` (`CheckConstraint`) — a type-based plan applies to every active asset of that type; frequency (Daily/Weekly/Monthly/Quarterly/Semiannual/Annual/Custom); `custom_interval_days`; optional `start_date`; the fields a generated work order needs (`category_id`, `priority_id`, `assigned_to_id`/`assigned_team`, `description`); `is_active` (soft delete) |
| `MaintenanceSchedule` | the **per-(plan, asset) "next due" tracker** — this is what resolves a type-based plan down to concrete assets, and what generation and the dashboard actually query. Unlike `AssetConditionHistory`, `next_due_date` is mutated in place, not append-only. Unique `(plan, asset)`; `last_work_order_id` points at the most recent PM work order it produced |

### Generation logic (`application/pm.py`)

A plain module, no Flask request-context assumptions — callable from tests, an admin
button, or a scheduled job identically:

- `_ensure_schedules(plan, today)`: lazily creates a `MaintenanceSchedule` for every
  matching active asset that doesn't have one yet, first due = `plan.start_date` or today.
  This means a newly-added asset of a monitored type, or a newly-activated plan, gets
  picked up automatically the next time generation runs — no migration or manual step.
- `generate_due_work_orders(today=None, user=None)`: for every schedule with
  `next_due_date <= today`, creates a `WorkOrder` (`source=PM`, location/site from the
  asset, category/priority/assignment from the plan), goes through
  `workflow.record_initial_status` (and `apply_transition` to `Assigned` if the plan has an
  assignee, exactly like `add_work_order`), then **advances `next_due_date` past today in
  the same transaction** — that advance is the entire duplicate-prevention mechanism, not a
  separate "already ran today" flag.
- Overdue catch-up: if a schedule is more than one cycle overdue (the job didn't run for a
  while), exactly **one** work order is still created, and `next_due_date` is advanced
  cycle-by-cycle from its original value until it's back in the future — no backlog of one
  work order per missed cycle.
- `dashboard_buckets(schedules, today=None)`: splits schedules into Overdue (`< today`),
  Due Today (`== today`), Due This Week (`today+1` .. `today+7`), Upcoming (`> today+7`).

### Scheduled job

`scheduled_jobs.run_pm_schedule_generation()` pushes an app context and calls
`generate_due_work_orders()`, logging what it created; registered unconditionally (no
Organization toggle, unlike the FTP job) in `main.py`'s `create_app()` as a daily 1am
APScheduler cron job.

### Routes / access model

Admin-only (matching Priority/Category): `maintenance_plans`, `add_maintenance_plan`,
`edit_maintenance_plan` (also shows the plan's schedules and their last-generated work
order), `delete_maintenance_plan` (soft). Staff-only (Admin/Specialist/Technician,
site-scoped for Technicians like the work order list): `pm_dashboard`. Admin-only:
`run_pm_generation` (POST, calls the generator directly and flashes the result — useful
both operationally and for testing without waiting for the cron trigger).

### Templates

`maintenance_plans.html`, `add/edit_maintenance_plan.html` (shared field partial,
edit shows the schedule table), `pm_dashboard.html` (four count tiles + four tables, admin
"Run PM Generation Now" button). Nav: "PM Dashboard" for staff, "PM Plans" under admin
Settings, both menus.

### Migration

`migrations/versions/bc1779acd4f1_add_maintenance_plan_and_schedule_tables.py` — two
`create_table`s including the check and unique constraints. Verified additive against a
simulated pre-Phase-4 baseline (stamp `cb141050cfff` → upgrade → autogenerate reports no
drift).

## What was tested

### Automated — `tests/test_pm.py` (17 tests), full suite 172 passing

- Plan CRUD: regular user 403; add a specific-asset plan; choosing both or neither target
  rejected; Custom frequency without an interval rejected.
- Generation: an asset-type plan creates one schedule + one work order per matching asset;
  **no duplicate work order when generation is re-run the same day, or a day later before
  the next cycle is due** (the plan's own defining requirement); **a 24-day-overdue weekly
  schedule produces exactly one work order** and lands on the correct next cycle date
  (verified against a hand-computed date, not just "some future date") — the plan's other
  defining requirement; `start_date` in the future correctly produces no work order yet and
  seeds the schedule at that future date; an inactive plan is skipped; an inactive
  (soft-deleted) asset is skipped even if its plan is active; an assignee on the plan
  produces a pre-`Assigned` work order.
- Dashboard: regular user 403; loads for staff; the four buckets split correctly against a
  hand-built set of dates including the exact 7-day boundary; the "run now" button is
  admin-only; a Technician at another site does not see another site's schedule.
- All 155 previously-passing tests still pass (see below for one fix required).

### Manual — dev server driven with `curl`, and a direct Python/DB check

Created two assets of one type; a Weekly plan with `start_date` five weeks in the past.
First "Run Now" created exactly one work order per asset (not one per missed week);
immediate second run created zero; dashboard showed both under Overdue initially and both
schedules cleared afterward; work order list showed both `PM: Weekly Fan Check` orders;
plan detail showed the schedule table with `start_date` reflected. Independently confirmed
at the DB level (bypassing HTTP) that exactly 2 `WorkOrder` rows with `source=PM` exist
after two generation calls, and both schedules' `next_due_date` landed on the correct next
Monday past the real run date. No nested `<form>`s on the two new detail-style pages;
server log clean.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## A cross-cutting bug found and fixed while adding these tests

`tests/test_work_orders.py` assumed the very first `WorkOrder` ever created in the whole
test session always gets id 1 (hardcoded as `wo_number == 'WO-000001'` in ~15 places). That
was true only because no other test module created a `WorkOrder` before it. Adding
`tests/test_pm.py` — which sorts alphabetically *before* `test_work_orders.py` and creates
its own PM work orders — broke that assumption and caused three unrelated-looking failures
in `test_work_orders.py` the first time the full suite ran together. Fixed by changing the
lookup helper to key off the work order's title (as every other cross-entity test helper in
this project already does) instead of an assumed number. Verified by running the test
files in several different orders. See `application/CLAUDE.md`'s new "Test-writing
gotchas" note — this is a project-wide rule now, not a PM-specific one.

A second, separate test-harness quirk was found and worked around in the same pass: issuing
an authenticated request with one test-client identity and then a different identity's
client *within the same test function* causes the second client's request to resolve
`current_user` as the *first* client's identity — reproduced even on a pre-existing Phase 1
route (`/sites`), so it predates this phase and isn't a Phase 4 regression. Every
site-/ownership-scoping test in this project already avoided this by construction (one
identity's HTTP requests per test, setup done directly via the ORM); the two new PM tests
that initially violated it were rewritten to match. Not root-caused (Flask-Login/Werkzeug
test client internals) — documented as a hard rule in `application/CLAUDE.md` instead.

## Deviations from the plan (and why)

1. **MaintenanceSchedule became a per-(plan, asset) row**, not a bare "next due date on the
   plan." The plan's own wording — "a plan tied to an Asset **or Asset Type**" — means a
   type-based plan covers many concrete assets; without resolving to one schedule row per
   asset, there'd be no way to know *which* asset a generated work order is for, or to show
   per-asset due dates on the dashboard. This is additive to the plan's model list, not a
   substitution.
2. **MaintenancePlan CRUD was added** even though only "models," "a background job," and
   "a dashboard section" were asked for — same reasoning as AssetType (Phase 2) and
   Priority/Category (Phase 3): a background job with no way to create the thing it acts on
   isn't a usable feature. Kept admin-only, matching the Priority/Category precedent.
3. **An admin "Run Now" button was added**, not asked for. It's the same code path as the
   cron job and made both manual verification and automated testing possible without
   manipulating wall-clock time or waiting for 1am.
4. **`start_date` on `MaintenancePlan` was added.** Without it, every newly-created plan's
   first occurrence defaults to "due today," which would immediately generate a work order
   on the very next job run — surprising for a plan meant to start next quarter.
5. **Frequency is a hardcoded set of choices in `application/pm.py`**, not an admin-editable
   database table like Priority/Category. The plan phrases it as a fixed list
   ("daily/weekly/monthly/..."), unlike priorities/categories which Phase 3 explicitly
   called out as admin-editable.
6. **Migration verified against a simulated baseline, not an empty DB** — same as every
   phase (see `PHASE_0_ARCHITECTURE_ANALYSIS.md` §5).
7. **Not done, deliberately:** Inspections (explicitly excluded by the prompt).

## Known follow-ups (not blockers)

- Browser/visual pass on the new templates.
- No UI to manually adjust a single schedule's `next_due_date` (e.g., to push one asset's
  occurrence out without editing the whole plan) — editing the plan's `start_date` only
  affects schedules that don't exist yet.
- `MaintenancePlan.assigned_team` free-text field still has no backing Team model, same
  open question noted in the Phase 3 report for `WorkOrder.assigned_team`.
- Nothing is committed yet — bootstrap + Phases 1–4 are all in the working tree.
