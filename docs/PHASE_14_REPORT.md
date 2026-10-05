# Phase 14 Report — Summer Walkthrough + Dashboard Redesign

Status: **complete** (backend + UI; the migration has been verified on a disposable
SQLite scratch DB but **not yet applied to the live MySQL database** — that needs a
separate, explicit `flask db upgrade` run, per this project's standing migration-safety
rule). User-requested, out-of-plan change: a 40-section spec for a K-12 M&O "summer
facility walkthrough" application. Rather than build a second, parallel system next to
the one that already does most of this (`Inspection`/`InspectionTemplate`/
`InspectionResult`/`WorkOrder`), this phase extends it — the same lesson Phase 13's
Ticket removal already taught this codebase about parallel systems.

## What the spec already had, under different names

| Spec concept | Existing equivalent |
|---|---|
| District → School → Building → Room | `Site` → `Facility` → `Room` (`Floor` in between) |
| Room checklist / inspection | `InspectionTemplate` / `InspectionItem` / `Inspection` / `InspectionResult` |
| Issue triage (Good/Issue/Critical) | `InspectionResult.result` = `Pass`/`Needs Attention`/`Fail` |
| Auto work order from a failed item | `inspections.generate_work_order_for_failures()` |

## What was added

- **Models**: `InspectionCycle` (name, start/end date, is_active); `Inspection.cycle_id`
  (nullable FK, additive); `InspectionAttachment` (mirrors Facility/Room/Asset's
  one-table-per-entity attachment pattern). Migration `6022eeaa1e8d`. On SQLite the
  `cycle_id` FK constraint is skipped (same reasoning, and same fix, as `a370d33e0db2`'s
  `WorkOrder.vendor_id` — SQLite's batch-recreate for a new FK on an existing table needs
  every existing constraint to be named, and none of this project's are); MySQL gets the
  real constraint.
- **`application/inspections.py`**: `start_walkthrough(facility, cycle, template, user)`
  bulk-creates one Scheduled Inspection per active Room, idempotently (skips rooms that
  already have one for that cycle+template — same lazy-creation precedent as `pm.py`'s
  `_ensure_schedules`). `next_incomplete_room_inspection(inspection)` resolves the next
  room in floor-then-room-number order. `inspection_progress(cycle_id, f, facility_ids)`
  computes per-facility and district rooms_total/rooms_inspected/pct/issues_open/
  critical_count, plus completed/in-progress/not-started facility counts — one GROUP BY
  per factor, same shape as `analytics.facility_health_scores()`.
- **Routes**: `/inspection_cycles` + add/edit (admin CRUD, like Priority/Category);
  `/start_walkthrough` (`is_staff()` — same access level as the existing one-at-a-time
  `add_inspection`); `/walkthrough/<inspection_id>` (the fast-mode screen). Download/delete
  routes for `InspectionAttachment`, mirroring Room's. `record_inspection_results()` gained
  an optional photo save (`_save_attachment`, reused unchanged) and a `walkthrough=1` form
  flag that redirects to the next incomplete room instead of `edit_inspection` — the
  write path itself (results, work-order generation, notifications) is completely
  untouched, so every pre-existing inspection test still exercises the same code.
- **Fast walkthrough UI** (`walkthrough_room.html`): large Good/Issue/Critical buttons
  built from a hidden radio group + styled sibling `<label>`s (keyboard/screen-reader
  accessible, no JS needed beyond a "mark room complete" shortcut that checks every
  "Pass" radio and submits) — maps onto `InspectionResultsForm` unchanged.
- **Dashboard** (`mo_dashboard.html` / `analytics.build_dashboard()`): a district
  completion gauge (Chart.js doughnut configured as a gauge, new `type: 'gauge'` branch
  in `mo_dashboard.js` with a center-text plugin), Schools Completed/In Progress/Not
  Started KPI tiles, and a per-school progress-bar list — color-coded on the spec's
  90/75/50/25% thresholds (`pct_tone`/`pct_status_label` macros). A cycle picker was
  added to the existing filter form; `parse_filters()` stays DB-free (a pure-unit-test
  contract an earlier version of this change broke), with the "default to the active
  cycle" resolution happening in `build_dashboard()` instead, which always has a DB
  context.
- **Facilities page**: table → card grid, each card showing completion %, rooms
  inspected/total, and open/critical issue counts for the active cycle; the same block
  was added to the facility detail page (`edit_facility.html`).
- **Palette**: primary/accent swapped to a user-supplied brick-red/amber-orange/cream
  scheme (separate request, same session); shadow scale upgraded to layered elevation;
  thin themed scrollbars; subtle card hover elevation.

## Explicitly deferred to a later phase

Per-room-type checklist templates (today one template applies to every room in a
walkthrough — `InspectionItem` has no room-type scoping yet); a separate `Issue` model
with its own type/severity fields (Pass/Needs Attention/Fail already covers the spec's
Good/Issue/Critical triage); the Kanban work-order board; offline/service-worker sync;
PDF/Excel export; SSO/Entra ID; multi-year side-by-side comparison UI (the data is
already captured via `cycle_id`, so this is pure reporting work later, no schema change
needed).

## Verification

`uv run pytest -q` — 499 passed. The migration was verified by building a disposable
SQLite baseline matching the pre-Phase-14 schema (hand-reflected, since the real
`inspection` table's FK-bearing new column can't be dropped from a SQLite table that
already has it — SQLite refuses to drop a column referenced by a FOREIGN KEY
constraint), stamping it at the previous head, autogenerating, and confirming a second
`db migrate` reports no further changes except the expected, by-design SQLite FK gap.
The live database has **not** been touched.
