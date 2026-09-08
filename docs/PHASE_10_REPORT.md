# Phase 10 Report — Reports & Exports

Status: **complete**. Definition of done met — all 13 listed reports with date/site (and
report-specific) filtering and CSV export; 43 new tests, 368/368 total, no regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_9_REPORT.md`, `../application/CLAUDE.md`.

## What was built

No new models, no new migration — this phase is a tabular, CSV-exportable view over metrics
Phases 6–9 already defined, plus a couple of straightforward row-level queries (Work Order,
Asset Condition, Asset Maintenance History) that had no prior report page.

### `application/reports.py`

One registry (`REPORTS`, ordered by `REPORT_ORDER`) mapping each of the 13 report keys to a
title, group, the date column it filters on (shown in the UI as a hint), which of the common
filter widgets apply, a default date preset, and a `rows(filters)` function returning
`(headers, rows, truncated)`. Every row is a list of **plain values** — the same list renders
in the HTML table and the CSV file, so there's exactly one source of truth per report; `_fmt()`
is the one place a `Decimal`/`date`/`datetime`/`None` becomes a display string, applied
identically both places.

| Report | Reuses | Notes |
|---|---|---|
| Work Order | — | Every filtered work order, one row per WO, full field set |
| Open Work Order | — | Same columns, `status IN OPEN_STATUSES` |
| Overdue Work Order | — | Open + `due_date < today` |
| Preventive Maintenance | — | Current `MaintenanceSchedule` rows with the same Overdue/Due Today/Due This Week/Upcoming bucket labels the PM dashboard uses |
| Asset Condition | — | Current condition + latest assessment date (a `MAX(assessed_at)` subquery per asset, since `Asset` only caches the *score*, not when it was taken) |
| Facility Condition | `analytics.facility_health_scores()` | Same Phase 9 health score, one row per facility, every factor as its own column |
| Maintenance Cost | `costs.cost_rollup()` | Same nine dimensions as the existing Cost Rollups page, now filterable by date/site and exportable |
| Technician Productivity | — | Assigned/completed counts from `WorkOrder`, labor hours/cost from `WorkOrderLabor.technician_id` (not `assigned_to_id` — a technician can log hours on work assigned to someone else) |
| Vendor Performance | `vendors.compute_vendor_stats()`, `expiration_status()` | Same Phase 6 page, now date-filterable and exportable |
| Asset Maintenance History | — | Merges `AssetConditionHistory` and completed-or-not `WorkOrder` rows for the asset(s) in scope into one date-sorted timeline |
| Capital Replacement | `risk.calculate_risk()` | Same Phase 8 score; unlike the live Capital Replacement page (always all-time), this report can scope the work-order history fed into the Failure Frequency / Maintenance Cost factors to the selected date range |
| SLA Performance | — | Per-priority due-date compliance (completed-with-a-due-date count, met-on-time count, %) plus an "All Priorities" total row — the same due-date-based definition Phase 9 used for its aggregate SLA Compliance KPI |
| Recurring Problems | `analytics.detect_recurring_issues()` | Same Phase 9 detector, listed with every member work order's number |

### Filters

`reports.parse_filters()` returns a superset of Phase 9's `analytics.parse_filters()` shape
(adds `vendor_id`, `asset_id`, `dimension`) specifically so the Facility Condition and
Recurring Problems reports can hand their filter dict straight to `analytics.py`'s functions
unchanged — analytics.py reads only the keys it needs and ignores the rest. Every report gets
a date preset (Week/Month/Quarter/Year/**All Time**/Custom); each report's registry entry
sets a sensible default (point-in-time reports like Open/Overdue Work Order and Capital
Replacement default to "All Time" so a currently-open item created outside a narrow window
still shows up; period reports like Work Order, Maintenance Cost, SLA Performance default to
"This Quarter"). Site scoping matches every other M&O view (`_visible_site_ids()`;
Technicians locked to their own site). Report-specific extra filters (facility, technician,
vendor, category, priority, asset, cost-rollup dimension) only render when that report's
registry entry lists them as relevant.

### Route, access model, templates

`reports_index` (`/reports`, grouped list) and `view_report` (`/reports/<key>`) — both
staff-viewable (Admin/Specialist/Technician), matching Cost Rollups/Technician
Workload/Vendor Performance's precedent; `is_staff()` gates both. `?format=csv` on the exact
same URL (same filters) streams the identical rows as a download instead of rendering the
page — `to_csv()` writes the same `headers`/`rows` through `csv.writer`. The on-screen table
slices to `DISPLAY_ROWS` (200) with a note to download the CSV for the rest; the CSV itself
gets the full result, capped at `MAX_EXPORT_ROWS` (20,000) with a `truncated` flag rather than
silently dropping rows — the same "never load an unbounded set" rule Phase 9 applied, now
also applied to the file, not just the browser. One generic `report_view.html` renders every
report (title, filter form, row count/truncation note, Export CSV button, data table); one
`reports.html` lists all 13 grouped by category. Nav: "Reports", both menus.

## A real bug found and fixed: nav crash on most existing pages

While wiring the "Reports" nav link's active-state highlighting I wrote
`{% if current_path.startswith('/reports') %}`. Every pre-existing check in `nav.html` uses
`==` (`{% if current_path == '/pm_dashboard' %}`), which is silently `False` when
`current_path` isn't in the template context — most `add_X`/`edit_X` routes only pass
`current_page_name`, not `current_path`. Jinja's `Undefined` tolerates a comparison but raises
`UndefinedError` on a method call. The full-suite run (every page renders the shared nav
include) immediately turned this into 68 failures across nine unrelated test files —
`add_facility`, `edit_work_order`, `add_project`, `add_inspection_template`, labor/material
routes, and more — none of which touch Phase 10 code at all. Fixed by using `request.path`
(always present in every Jinja render) instead of the possibly-unpassed `current_path`
context variable, documented in `application/CLAUDE.md` as a hard rule for any future
prefix/method-based nav check.

A second issue caught the same way, one commit earlier: renaming Phase 9's `_parse_date`/
`_days_between`/`_num`/`_pct` to public names (`parse_date`/`days_between`/`num`/`pct`) so
`reports.py` could reuse them without reaching into another module's private helpers exposed
a local-variable shadowing bug already latent in `analytics.build_insights()` — a local
`pct = round(...)` inside one `if` block shadowed the now-imported `pct()` function for the
rest of the function, breaking a *different* insight rule two lines later
(`TypeError: 'int' object is not callable`). Renamed the local variable; not a bug the rename
introduced, but one the rename's exposure of the module-level name surfaced.

## What was tested

### Automated — `tests/test_reports.py` (43 tests), full suite 368 passing

- CSV formatting: every value type (`Decimal`, `date`, `datetime`, `None`) through `to_csv()`
  and parsed back with `csv.reader`, asserting the exact string in each cell; a full report's
  CSV round-tripped the same way; the truncation cap flags without dropping silently.
- `parse_filters()`: "All Time" produces no date bound; a report's own default preset applies
  when the caller doesn't override it; the extra fields (`vendor_id`/`asset_id`/`dimension`)
  parse; a site-locked user's `site_id` arg is ignored.
- Every report's row builder against seeded data with hand-computed expected values: date
  windows include/exclude the right rows; Work Order's cost prefers `CostRecord` over
  `actual_cost` (same rule as every other cost figure in this project); Open/Overdue
  correctly separate open-regardless-of-age from past-due-and-still-open; PM bucket labels;
  Asset Condition's date filter requiring an actual assessment (an unassessed asset appears
  only in the all-time view); Facility Condition returns a scored row; Maintenance Cost's
  facility-dimension sums and its category-dimension switch; Technician Productivity's
  completed count/avg resolution/labor hours/labor cost all hand-computed from labor entries
  keyed by `technician_id`, not `assigned_to_id`; Vendor Performance's totals match
  `compute_vendor_stats()` called directly; Asset Maintenance History merges and sorts a
  condition entry and a work order into one timeline, newest first; Capital Replacement's
  score and factor count match `risk.calculate_risk()` called directly on the same asset; SLA
  Performance's per-priority met/missed counts and the "All Priorities" total row; Recurring
  Problems detects a 3-work-order group and lists every member's number.
- Routes: login required; a regular user gets 403 on the index; an admin gets 200; an unknown
  report key 404s; every one of the 13 reports renders (200) and exports a valid CSV (correct
  `Content-Type`/`Content-Disposition`, parseable, at least a header row) — parametrized over
  the full registry so a 14th report added later is covered automatically; a CSV download's
  actual cell values matched known seeded data end to end; a Technician's CSV export excludes
  another site's work order.

### Performance verification beyond the unit test

Captured every `work_order` SELECT issued by all 13 reports (via a `before_cursor_execute`
listener) and ran `EXPLAIN QUERY PLAN` on each: **11 of 11 use an index, 0 full scans**.

### Manual — dev server driven with `curl`

Seeded one facility, three assets (mixed condition), a vendor, a PM plan, and 20 work orders
with labor entries across ~100 days. Every report's HTML and `?format=csv` returned 200 with
plausible row counts (Work Order 15 rows for the default quarter window vs. 21 for
`preset=all`; Maintenance Cost's `dimension=category` correctly summed all 20 work orders'
estimated/actual cost); correct `Content-Type: text/csv` and `Content-Disposition: attachment;
filename="<key>.csv"`; a Technician (staff) could view a report; server log clean throughout.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **PDF/Excel export not built** — per the phase's own instruction and
   `PROJECT_PLAN.md`'s non-goals ("PDF/Excel export is 'nice to have per report,' not a
   blocking requirement... CSV export is the baseline"), and it isn't cheap given the stack
   (no PDF/Excel library currently a dependency). Listed as a follow-up.
2. **The phase prompt's "section on reporting" in PROJECT_PLAN.md doesn't exist** —
   PROJECT_PLAN.md has no section by that name; only the Canonical KPI List and Scored/
   Calculated Features section, which is what every report actually draws from (same class
   of gap as Phase 9's referenced-but-missing "dashboard focus areas per role").
3. **Technician Productivity keys labor hours/cost by `WorkOrderLabor.technician_id`**, not
   `WorkOrder.assigned_to_id` — the plan's KPI is about who did the work, and Phase 7 already
   distinguishes "who logged this labor entry" from "who the work order is assigned to."
4. **Capital Replacement's date filter scopes the work-order history fed into risk
   calculation**, deliberately differing from the live (always all-time) Capital Replacement
   page from Phase 8 — a report answering "what did risk look like based on this period's
   activity" is a reasonable, distinct question from the live page's "what is risk right now."
5. **SLA Performance reuses the due-date-based definition Phase 9 introduced as a
   placeholder**, broken out per priority — not a new definition, and still due for
   replacement once Phase 11 adds a real per-priority SLA target model.

## Known follow-ups (not blockers)

- PDF/Excel export, if ever requested — would need a new dependency (e.g. `openpyxl` or a
  PDF renderer) and isn't required by the plan.
- Phase 11's SLA model should replace the due-date placeholder in both `analytics.py`'s
  aggregate KPI and this phase's per-priority report with real configurable targets.
- `MAX_EXPORT_ROWS` (20,000) is a hard cap, not true pagination/streaming — fine at current
  scale; worth revisiting if a district's full-history CSV export regularly exceeds it.
- Browser/visual pass on `reports.html` and `report_view.html`.
- Nothing is committed yet — bootstrap + Phases 1–10 are all in the working tree.
