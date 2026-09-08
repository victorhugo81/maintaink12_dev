# Phase 7 Report — Labor & Cost Tracking

Status: **complete**. Definition of done met — labor tracking, cost rollups across every
named dimension, and technician workload; 28 new tests, 243/243 total, no regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_6_REPORT.md`, `../application/CLAUDE.md`.

## What was built

### Models (`application/models.py`)

| Model | Notes |
|---|---|
| `WorkOrderMaterial` | Not in the plan's numbered model list for this phase, but named as the source of "material cost" in the same phase's own CostRecord description, and never built in Phase 3 (deferred like Vendor/Inspection). Added now since nothing else could supply it. `total_cost` is a computed property (`quantity * unit_cost`), never stored |
| `WorkOrderLabor` | `technician_id` required; `start_time`/`end_time` optional; `labor_hours` is auto-computed from the time difference when both are given (overriding any manually-typed value — the times are more precise), otherwise entered directly; `labor_type` ∈ Regular/Overtime/Emergency/Contractor; `labor_cost` is a computed property (hours × rate, $0 if no rate — hours still count toward workload even with no cost) |
| `CostRecord` | One row per WorkOrder (unique `work_order_id`), created lazily on first use — a cost-summary **cache**, not a source of truth. `labor_cost`/`material_cost`/`total_cost` are recomputed by `refresh_cost_record()` every time a labor or material entry changes; `vendor_cost`/`other_cost` are the only fields entered directly |

### Business logic (`application/costs.py`)

- `refresh_cost_record(work_order)` — the single place a CostRecord is created or updated;
  sums the WorkOrder's current labor/material entries, preserves whatever vendor/other cost
  is already set, recomputes `total_cost`. Called after every labor/material add or delete.
- `cost_rollup(work_orders, dimension)` — groups a list of WorkOrders by one of nine
  dimensions (`work_order`, `facility`, `site`, `asset`, `category`, `team`, `vendor`,
  `month`, `year`), returning `{label, count, estimated, actual}` per group. `actual`
  prefers `CostRecord.total_cost`, falling back to `WorkOrder.actual_cost` for work orders
  with no labor/material logged yet — so the rollup stays meaningful across a mix of
  granularly-tracked and manually-totaled work orders.
- `technician_workload(technicians, work_orders_by_tech, today=None)` — per-technician
  open/in-progress/overdue/due-today/due-this-week counts, the same bucketing shape as the
  PM and Inspection dashboards, applied per-person instead of per-schedule.

### Two interpretation calls, made and documented rather than guessed silently

1. **`WorkOrderMaterial` was built even though it's not in the numbered list** — the
   phase's own item 2 says CostRecord aggregates "material cost (from WorkOrderMaterial
   entries...)", and that model never existed. Same category of call as adding `AssetType`
   in Phase 2 or `MaintenancePlan` CRUD in Phase 4: a named dependency with no other source.
2. **"Department" has no backing model anywhere in this schema.** PROJECT_PLAN.md lists it
   as a rollup dimension alongside Category without ever defining a Department model, and
   this phase's own list doesn't ask for one. Mapped to `WorkOrder.assigned_team` (falling
   back to the assignee's name, then "Unassigned") — the closest existing organizational-
   unit concept already on WorkOrder since Phase 3.

### Routes / access model

Labor/material/cost actions on a work order (`add_work_order_labor`, `delete_work_order_labor`,
`add_work_order_material`, `delete_work_order_material`, `update_work_order_cost`) use the
same `can_manage_work_order` gate as status changes: Admin/Specialist always, Technician
scoped to their own site. `cost_rollups` and `technician_workload` are staff-viewable
(Admin/Specialist/Technician), site-scoped for Technicians — matching the Vendor
performance view's precedent from Phase 6.

### Templates

Three new sections on `edit_work_order.html` (Labor, Materials, Cost Summary — each its own
non-nested `<form>`, following the established convention), `cost_rollups.html` (a
dimension-selector dropdown driving one table, not nine separate always-visible ones),
`technician_workload.html`. Nav: "Cost Rollups" and "Technician Workload" for staff, both
menus.

### Migration

`migrations/versions/18f317e4f1a5_add_work_order_labor_material_and_cost_.py` — three
`create_table`s (no changes to any existing table this time, unlike Phase 6). Verified
additive against a simulated pre-Phase-7 baseline with zero drift.

## A real bug found and fixed: duplicate CostRecord creation

`update_work_order_cost` originally did its own get-or-create on `wo.cost_record`, then
called `refresh_cost_record()` — which does the *same* get-or-create internally. The second
call's `work_order.cost_record` lookup returned a stale `None`: SQLAlchemy only
back-populates a scalar relationship through relationship-attribute assignment
(`wo.cost_record = record`), not by matching a freshly-inserted row's raw foreign-key
column against an already-cached (empty) relationship read. The second get-or-create then
tried to `INSERT` a second `CostRecord` with the same `work_order_id`, tripping the unique
constraint and raising an `IntegrityError` on commit — caught immediately by the test suite
(`test_vendor_and_other_cost_added_directly` failed on first run). Fixed by making
`refresh_cost_record()` the single call site: the route calls it once, gets the correctly
resolved record back, and only then sets `vendor_cost`/`other_cost` on that same object.
Documented as a hard rule in `application/CLAUDE.md` — the classic "get-or-create pattern
duplicated across two call sites in one request" trap.

A second, minor, test-only bug (not an application bug) surfaced alongside it: a test
asserted a technician's name rendered as `"Workload Tech"`, but `User.get_full_name()`'s
format string always includes a literal space for the (empty) middle name, producing
`"Workload  Tech"` — a pre-existing AssistItK12 formatting quirk, not something this phase
touched. Fixed the test assertion, not the app.

## What was tested

### Automated — `tests/test_labor_and_costs.py` (28 tests), full suite 243 passing

- Labor: regular user 403; direct-hours entry computes the correct cost; start/end time
  auto-computes hours (2.5 from 08:00–10:30), overriding any submitted hours value;
  end-before-start rejected; missing both hours and times rejected; delete an entry.
- Materials: add (asserts `total_cost` = quantity × unit_cost); delete.
- CostRecord refresh: creating labor + material entries produces the correct summed
  `labor_cost`/`material_cost`/`total_cost`; removing a labor entry recalculates the total
  back down; vendor/other cost set directly; **vendor/other cost survives a subsequent
  labor add** (the exact scenario the duplicate-creation bug above would have broken,
  tested explicitly, not just incidentally); regular user 403 on cost updates.
- `cost_rollup()` unit tests (no Flask, fake WorkOrder-like objects): grouping by category
  sums count/estimated/actual correctly across multiple work orders in the same group;
  falls back to `actual_cost` when no CostRecord exists; groups correctly by month and by
  year from `created_at`; groups by team with the assigned-team → assignee-name →
  "Unassigned" fallback chain exercised for all three cases in one test; unknown dimension
  raises.
- Cost rollup route: regular user 403; the page loads successfully for **every** dimension
  in `ROLLUP_DIMENSIONS`, not just one; an invalid dimension falls back to facility rather
  than erroring.
- `technician_workload()` unit tests: a technician with a mix of New/In Progress/Assigned/
  Completed work orders gets the exactly-right bucket counts (Completed correctly excluded
  entirely, not just from "open"); a technician with zero work orders shows all zeros.
- Technician workload route: regular user 403; staff 200; an assigned technician's name
  appears on the rendered page.
- Regression: ticket routes and an existing work order detail page still load.

### Manual — dev server driven with `curl`

Created a work order with `estimated_cost=200`, logged 3 hours at $25/hr ($75) and 2 units
at $30 ($60) — confirmed the detail page showed $135 total actual before any vendor/other
cost. Added $50 vendor cost + $10 other cost — confirmed the total updated to exactly $195
(135 + 50 + 10). Confirmed the facility cost rollup showed the same $195 actual against
$200 estimated for that facility. Confirmed the technician appeared on the workload page.
No nested `<form>`s on any of the three new/modified pages (8 forms on the work order
detail page, max nesting depth 1); server log clean throughout.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **`WorkOrderMaterial` added** — see the interpretation-call section above.
2. **"Department" mapped to `assigned_team`** — see the interpretation-call section above.
3. **Cost rollup is one dimension-selector page, not nine separate always-visible
   tables** — matches the UX precedent set by every other multi-dimension view in this
   project (PM/Inspection dashboards use fixed buckets since there are only four; a
   nine-dimension rollup is better served by a selector, same reasoning as Vendor
   Performance being one page rather than nine).
4. **`labor_hours` auto-computed from start/end time when both are given** — the plan lists
   "start/end time" and "labor hours" as separate fields without specifying which wins;
   deriving hours from precise timestamps when available, falling back to a manual figure
   otherwise, is the more accurate choice and avoids the two ever silently disagreeing.
5. **`technician_workload()` scopes strictly to `role_id == 3`** (Technician), not
   Specialists too — matches the plan's literal wording ("Technician workload calculation
   ... per technician").
6. **Migration verified against a simulated baseline, not an empty DB** — same as every
   phase (see `PHASE_0_ARCHITECTURE_ANALYSIS.md` §5). This phase only creates new tables,
   so — unlike Phase 6 — the simpler "skip the new tables" baseline trick was sufficient.

## Known follow-ups (not blockers)

- `cost_rollup()` and the vendor performance view (Phase 6) both aggregate in Python over
  a fully-loaded work order list rather than a SQL `GROUP BY` — fine at current scale, the
  same tradeoff already flagged in `PHASE_0_ARCHITECTURE_ANALYSIS.md` §8; worth revisiting
  alongside Phase 9's dashboards if work order volume grows large.
- Browser/visual pass on the new templates and the three new work-order-detail sections.
- No CSV import for bulk labor/material entry (Phase 11's explicit scope).
- Nothing is committed yet — bootstrap + Phases 1–7 are all in the working tree.
