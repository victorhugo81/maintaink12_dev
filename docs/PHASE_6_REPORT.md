# Phase 6 Report — Vendors

Status: **complete**. Definition of done met — Vendor model/CRUD, WorkOrder linking, and a
performance/cost/expiration view; 21 new tests, 215/215 total, no regressions.

Note on scope: the plan's Phase 6 originally also covered Inventory tracking
(`InventoryItem`/`InventoryTransaction`, low-stock alerts, decrement-on-use). The user
explicitly removed that from the plan before this phase was built — see the updated
`docs/MAINTAINK12_PLAN_AND_PROMPTS.md` (Phases 6, 7, and 11 were all adjusted so the plan
stays internally consistent; Phase 7's CostRecord now sources material cost from
`WorkOrderMaterial` entries directly rather than "Phase 6 inventory"). Nothing below
implements Inventory in any form.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_5_REPORT.md`, `../application/CLAUDE.md`.

## What was built

### Models (`application/models.py`)

`Vendor`: contact info, `contract_start_date`/`contract_end_date`,
`insurance_expiration`, `license_expiration`, `notes`, `is_active`. Standalone reference
data — no CheckConstraint or required-target logic like `MaintenancePlan`/`Inspection`
needed, since a vendor doesn't point at anything.

`WorkOrder.vendor_id` (nullable FK): the one link between the two, added to the **existing**
`work_order` table — the first migration in this project to `ALTER` an existing table
rather than only create new ones.

### Business logic (`application/vendors.py`)

- `expiration_status(exp_date, today=None)` → `'expired'` / `'expiring_soon'` (within 30
  days, inclusive of the boundary) / `'ok'` / `None` (no date set).
- `compute_vendor_stats(work_orders)` → `total_count`, `open_count` (via
  `workflow.OPEN_STATUSES`, the same set the work order list's "All Open" filter uses),
  `total_cost` (sum of `actual_cost`, treating unset as 0), `avg_completion_days` (mean of
  `completed_at - created_at` in days over only the *completed* work orders — `None`, not
  `0`, when there are none, so an untouched vendor doesn't misleadingly show "0 days").

### Routes / access model

Admin-only (matching every other M&O reference-data list): `vendors`, `add_vendor`,
`edit_vendor`, `delete_vendor` (soft — `WorkOrder.vendor_id` is nullable, so nothing blocks
deactivating a vendor with historical work orders; they simply keep pointing at the now-
inactive record). Staff-viewable (Admin/Specialist/Technician): `vendor_performance` — one
aggregate table across every active vendor, not a per-vendor drill-down page, matching the
plan's own phrasing ("Vendor performance view: work orders by vendor, cost by vendor...").
The vendor field on work orders themselves is edited through the existing staff
`WorkOrderForm`/`edit_work_order` (admin/specialist/technician, site-scoped) — no separate
"assign vendor" route was needed.

### Templates

`vendors.html`, `add/edit_vendor.html` (shared `includes/vendor_fields.html`),
`vendor_performance.html`. `includes/work_order_fields.html` gained a Vendor select
alongside Assigned To/Team/Scheduled/Due Date. Nav: "Vendor Performance" for staff,
"Vendors" under admin Settings, both menus.

## A real, dialect-specific migration bug found and fixed

Autogenerate's first attempt wrapped the `vendor_id` column-plus-FK addition in
`op.batch_alter_table(...)` (flask-migrate's `render_as_batch` default, applied
unconditionally on every backend). On SQLite, adding a *new foreign key constraint* to an
*existing* table forces batch mode into its "recreate the whole table" strategy, and that
recreate requires every one of the table's other constraints to be reflectable with a
name — none of this project's FKs have one (plain `db.ForeignKey(...)`, no explicit
`name=`). Applying the migration failed with `ValueError: Constraint must have a name`.

This was verified to be SQLite-specific, not a real production risk: MySQL (the actual
deployment target per `config.py`) executes a plain `ADD COLUMN ... ADD CONSTRAINT` on an
existing table natively, with no table recreation involved, so it never hits this
limitation. The fix makes the migration dialect-aware — `op.get_bind().dialect.name` gates
the `create_foreign_key` call to non-SQLite backends only; SQLite gets the column and index
(a genuinely simple, non-recreating ALTER) and the ORM-level relationship
(`WorkOrder.vendor` / `Vendor.work_orders`) still works for every query and join the
application performs — it just lacks a *database-enforced* constraint on that one column
when running under SQLite specifically.

Getting a trustworthy baseline to test this against also took real care: the "skip the new
table(s), `create_all()` everything else" trick used successfully in every prior phase only
works when a migration exclusively creates *new* tables. Here, the existing `work_order`
table itself needed to change (minus one column), and naively rebuilding it via
`Column.copy()`-filtered columns lost enough constraint fidelity that autogenerate reported
every other foreign key on the table as "newly added" too — a false signal from the
reconstruction, not a real problem. The reliable technique (documented in
`application/CLAUDE.md`) was compiling the table's actual `CREATE TABLE` DDL, stripping just
the one column's line via regex, and executing that directly.

## What was tested

### Automated — `tests/test_vendors.py` (21 tests), full suite 215 passing

- CRUD: regular user 403; add; duplicate name rejected; edit; deactivating a vendor with a
  linked work order leaves both the vendor row and the work order's `vendor_id` intact
  (soft delete, not a cascade).
- Linking: a vendor is set on an existing work order through the staff edit form; a regular
  user's attempt to do the same is rejected (403).
- Performance view: regular user 403, staff 200; cost/count aggregation against three work
  orders with different statuses and costs (including one with no `actual_cost` set,
  correctly treated as 0, and confirming `open_count` matches `workflow.OPEN_STATUSES`
  exactly rather than a hand-rolled status list); average completion time computed from two
  completed work orders while a third, still-open one is correctly excluded from the
  average rather than skewing it; a vendor with zero work orders reports `None` for the
  average, not a misleading `0`.
- Expiration status: no date → `None`; a past date → `'expired'`; today and the exact
  30-day boundary → `'expiring_soon'` (both boundary conditions checked explicitly, not
  just "somewhere in the middle"); 31 days out → `'ok'`; an expiring vendor's contract
  renders with the warning badge class on the actual rendered performance page, not just
  in the pure function.
- Regression: ticket routes and the work order list still load, confirming the `work_order`
  table alteration didn't disturb anything else.
- All 194 previously-passing tests still pass, both in normal order and reordered
  (`test_vendors.py` → `test_work_orders.py` → `test_pm.py` → `test_inspections.py`) to
  check for the class of cross-module fragility Phase 4 found.

### Manual — dev server driven with `curl`

Created a vendor with a near-term contract expiration and a distant insurance expiration;
linked it to a Manual work order with a cost via the staff form; confirmed the work order
detail page shows the vendor name; confirmed the vendors list highlights the near-term
contract date in the warning color and leaves the distant insurance date unhighlighted;
confirmed the performance page shows the correct total cost and the correct badge colors
for each expiration type independently. No nested `<form>`s; server log clean.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **Inventory was cut entirely**, per explicit user instruction before this phase began —
   see the scope note at the top of this report.
2. **The FK constraint is skipped on SQLite specifically** — a necessary, verified-safe
   workaround for a real Alembic/SQLite limitation, not a design preference. See the
   dedicated section above.
3. **One aggregate performance table, not a per-vendor detail page** — matches the plan's
   own phrasing ("Vendor performance view: work orders by vendor, cost by vendor...", read
   as one view listing every vendor's numbers side by side).
4. **Vendor assignment reuses the existing staff `WorkOrderForm`/`edit_work_order` route**
   rather than a dedicated "assign vendor" action — consistent with how `assigned_to_id`
   and `assigned_team` are already just fields on that same form.
5. **Migration verified against a from-scratch-compiled, column-stripped baseline**, not the
   simpler "skip new tables" trick from every prior phase — necessary because this is the
   first phase to alter an existing table (see above); not a deviation from the plan's own
   scope, just a harder version of the same verification standard.

## Known follow-ups (not blockers)

- Browser/visual pass on the new templates.
- No CSV import or bulk vendor onboarding (that's Phase 11's explicit scope).
- The performance view queries work orders per-vendor in a loop (one query per active
  vendor) rather than a single aggregate `GROUP BY` — acceptable at this scale, but the
  same N+1-adjacent pattern already flagged for the ticket dashboard in
  `PHASE_0_ARCHITECTURE_ANALYSIS.md` §8; worth revisiting alongside Phase 9's dashboards if
  vendor counts grow large.
- Nothing is committed yet — bootstrap + Phases 1–6 are all in the working tree.
