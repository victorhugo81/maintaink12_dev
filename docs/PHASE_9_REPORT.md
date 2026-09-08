# Phase 9 Report — Dashboards & Analytics

Status: **complete**. Definition of done met — four role dashboards, Facility Health Score,
Recurring Issue Detection, Operational Insights, all named charts, and consistent date/filter
handling; 38 new tests, 325/325 total, no regressions; every dashboard `work_order` query
verified to use an index.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_8_REPORT.md`, `../application/CLAUDE.md`.

## What was built

No new models. One new index (`work_order.completed_at`, migration `d919f88115eb`), one new
module (`application/analytics.py`), one new route (`/dashboard`), one new template plus a
chart script, and two detail-page additions.

### `application/analytics.py`

Same two-layer shape as pm.py / costs.py / risk.py — pure functions that tests call directly,
and query functions that feed them — with one deliberate difference: the query layer
aggregates **in SQL**. `PHASE_0_ARCHITECTURE_ANALYSIS.md` §8 flagged that the ticket dashboard
loads every ticket into Python twice per view and said Phase 9 should do bucketed counts in
SQL; the plan's performance baseline says the same. So every KPI is a `GROUP BY` /
`SUM(CASE ...)` over indexed `work_order` columns, and the only row-level scan is the
recurring-issue detector, which reads at most `RECURRING_SCAN_LIMIT` (5,000) of the newest
work orders in the filter window as eight plain columns, never ORM objects.

| Piece | Notes |
|---|---|
| `parse_filters()` / `_wo_clauses()` | One filter dict (preset → inclusive start/end, site/facility/team/technician/category/priority/status) and one function that turns it into SQLAlchemy clauses. Every widget goes through it — a filter can't apply to one chart and not another. Point-in-time KPIs (Total Open, Overdue, PM Due, ...) skip the date range on purpose; "in period" ones use it |
| `work_order_kpis()` | All 15 Work Order KPIs in three aggregate queries. Avg Response/Resolution are `AVG()` of a dialect-aware day-difference expression (`julianday` on SQLite, `TIMESTAMPDIFF` on MySQL) — no per-row Python |
| `facility_kpis()`, `maintenance_kpis()`, `cost_kpis()` | Facility / Maintenance / Cost KPI lists. Cost prefers `CostRecord.total_cost`, falling back to `WorkOrder.actual_cost` — Phase 7's rule, reused not redefined |
| `chart_data()` | Every chart in the phase's list, top-10 limited where the axis is unbounded, months filled with zeros across the range |
| `detect_recurring_issues()` (pure) | Groups by same asset, same room + category, same facility + category, same normalized title (lower-cased, punctuation and filler words stripped); recurring at 3+. A broader group whose members are all inside a more specific one already reported is dropped (asset ⊂ room ⊂ facility ⊂ description), a strict superset is kept |
| `facility_health()` (pure) | Seven factors → 0–100 health contribution each → mean of the available ones. Bucket tables for the judgment-free factors follow the Phase 8 risk-score precedent (fixed thresholds, documented) |
| `facility_health_scores()` | One `GROUP BY facility_id` per factor — six queries regardless of how many facilities are in scope |
| `build_insights()` (pure) | Eleven rules; each fires only when its inputs exist and clear a minimum sample/effect size, and every figure in the sentence is one of those inputs |
| `build_dashboard()` | Assembles a view. Technician/School Staff views overwrite the filter dict's technician/requester and site before any query runs, so a `view=` or `technician_id=` query arg can't widen what they see |

### Route, access model, templates

`mo_dashboard` (`/dashboard`, any logged-in user). Admins default to Executive, Specialists to
M&O Manager, and both may switch between those two; Technicians get Technician; everyone
else School Staff. `mo_dashboard.html` renders KPI cards per view, the Operational Insights
list, Chart.js canvases (the same Chart.js 4 bundle `index.html` already loads; data passed
as one `window.MO_DASHBOARD` JSON blob from a CSP-nonce'd inline script, drawn by
`static/js/mo_dashboard.js`), the Facility Health table with every factor and its underlying
figures, and the Recurring Issues table with linked work orders. `edit_facility.html` gained
a Health Score section (staff-visible, last 90 days for the period-based factors);
`edit_work_order.html` gained a "Recurring Issue" section listing sibling work orders when
the one being viewed belongs to a detected group. Nav: "M&O Dashboard", both menus. The
legacy ticket dashboard at `/` is untouched.

### Migration

`d919f88115eb_add_work_order_completed_at_index.py` — a single `create_index`. Verified
against a baseline built as the full current schema minus exactly that index: autogenerate
produced only the index, applied cleanly, re-run reported "No changes in schema detected."
Index creation inside `batch_alter_table` doesn't trigger SQLite's table-recreate path, so
the Phase 6/8 dialect workaround wasn't needed.

## Interpretation calls, made and documented rather than guessed silently

1. **The plan has no per-role "dashboard focus areas."** The phase prompt says to build the
   four dashboards "per PROJECT_PLAN.md's dashboard focus areas for each role" — that section
   doesn't exist anywhere in PROJECT_PLAN.md (checked every mention of "dashboard" and each
   role name). The views were derived from the Canonical KPI List instead: Executive = the
   high-level subset (open/overdue/emergency, completion and SLA rates, facilities/assets
   condition, PM/inspection status, cost totals, health scores, top-5 recurring); M&O
   Manager = every KPI, every chart, full recurring list, open-issues-by-facility;
   Technician = "my work" (open/in progress/overdue/due today/due this week, site PM and
   inspection status, my open list); School Staff = "my requests" plus open issues by
   facility at their site, no cost data.
2. **Roles.** The plan lists eight roles (District Administrator, M&O Director, ...) but the
   app has the four AssistItK12 role ids (1 Admin, 2 Specialist, 3 Technician, 4+ other) and
   no phase so far has added roles. Mapped: Admin → Executive default, Specialist → Manager
   default (both can switch), Technician → Technician, everyone else → School Staff.
3. **Undefined KPIs.** The Canonical KPI List names these without defining them; each
   definition is on the computing function's docstring: *Backlog* = open and created more
   than 30 days ago; *Completion Rate* = of work orders opened in the period, % now
   Completed/Closed; *SLA Compliance %* = of work orders completed in the period that had a
   due date, % completed on or before it (a due-date placeholder until Phase 11's per-
   priority SLA model exists — labelled "SLA Compliance (due date)" in the UI); *Repeat Work
   %* = % of work orders opened in the period that belong to a detected recurring group;
   *Assets Requiring Attention* = condition Fair or worse (Poor/Critical being the narrower
   listed KPI).
4. **"Department" filter** → `WorkOrder.assigned_team`, the same mapping Phase 7's cost
   rollup made for the same word, for the same reason (no Department model exists).
5. **"Background jobs for expensive aggregations."** The plan's baseline mentions them; the
   phase's own definition of done asks that queries be "paginated/indexed appropriately."
   With every aggregate in SQL on indexed columns and the one scan capped, the dashboard
   doesn't need a background job at this point; caching/precomputing is listed as a
   follow-up rather than built speculatively.

## Bugs found and fixed

- **Template double-escaping (real, caught by a test).** `chart_box('Response &amp;
  Completion Trends', ...)` — the `&amp;` inside a Jinja string literal is escaped again by
  autoescape, so the page showed `&amp;` literally. Fixed to a plain `&`; noted in
  `application/CLAUDE.md`.
- **Two test-side miscounts (mine, not the app's).** Facility Age for a 1985 building is
  41 years in 2026 (the 41–60 bucket → 45, not 65), and only two health factors are
  *always* available (Open/Overdue and Recurring Problems), so a no-data facility has
  `factor_count == 2`, not 3 — the same class of slip as Phase 8's risk `factor_count`.
- **Caught before testing:** aggregate queries whose first selected column belongs to the
  join target (`query(Facility.name, count(WorkOrder.id)).join(Facility, ...)`) leave
  SQLAlchemy unable to infer the join origin; every such query got an explicit
  `.select_from(WorkOrder)` (or `MaintenanceSchedule`/`Inspection`) before the first run.
- **Re-encountered, not a bug:** the test-client identity-poisoning quirk from Phase 3/4 —
  a smoke script that used a Technician client and then a School Staff client in one
  process saw the second render as the first. The tests use one identity per function, per
  the standing rule.

## What was tested

### Automated — `tests/test_dashboard.py` (38 tests), full suite 325 passing

- Pure date ranges: every preset against a fixed Wednesday, custom range accepted, inverted
  or missing custom range falls back to "month"; `parse_filters()` rejects bad preset/
  status/int args and ignores `site_id` for site-locked users.
- Recurring detection: below threshold → nothing; same asset ×3; room/facility/description
  kinds; the subset-drop rule (identical-membership description group dropped, strict-
  superset facility group kept alongside the asset group); single-vs-mixed facility id.
- Facility health: a full seven-factor case with every contribution hand-computed and the
  exact mean asserted; missing factors excluded (score 100 from the two always-available
  ones); overdue penalty floors at 0; below-average cost scores 100.
- Insights: each of the eleven rules fires with the exact sentence for known inputs, output
  is ordered danger → warning → info, and every rule stays silent below its thresholds.
- **KPIs against seeded data with known expected values** — seven work orders in a dedicated
  facility (2 New incl. one unassigned/overdue/40-days-old, 1 In Progress started 2 days
  after creation, 1 Waiting, 1 Completed on time in 4 days, 1 Closed late in 6 days from a
  PM source, 1 Cancelled): Total Open 4, New 2, Unassigned 1, In Progress 1, Waiting 1,
  Overdue 1, Emergency 1, Backlog 1, Opened 7, Completed 2, Completion Rate 2/7, Avg
  Response 2.0 d, Avg Resolution 5.0 d, SLA 50% of 2, PM share 1/7, Estimated $300 vs Actual
  $330 (both recorded on 2). A 20-day window drops the 40-day-old work order from period
  counts but not from point-in-time ones; priority and status filters apply to both.
- Cost prefers CostRecord over `actual_cost` (75 over 999) with labor/material/vendor split;
  PM overdue/due and inspection overdue/due (facility-, room- and asset-targeted); asset
  condition KPIs (attention 3, poor/critical 2, past life 1, full distribution incl.
  Unassessed); open issues by facility; chart series (status counts, month totals, top
  technician); end-to-end facility health factors from real rows; recurring detection and
  labelling from the DB; the work-order-detail sibling lookup (and its empty case).
- Routes: login required; Admin defaults to Executive, switches to Manager, and a disallowed
  `view=technician` falls back; filters change the rendered numbers; a bad custom range
  still renders; Technician forced to their own view with their work listed and no health
  table; School Staff forced to theirs with their request listed and no cost figures;
  facility page shows the health factors; work order page links a recurring sibling by
  number; the legacy `/` dashboard still loads.
- Performance: `EXPLAIN QUERY PLAN` on the open-KPI shape asserts `USING INDEX` and no
  `SCAN work_order`.

### Performance verification beyond the unit test

Captured every `work_order` SELECT the Manager view issues via a `before_cursor_execute`
listener and ran `EXPLAIN QUERY PLAN` on each: **21 of 21 use an index, 0 full scans** with
a facility filter; district-wide (no filters) the point-in-time queries use
`ix_work_order_status` and the period queries `ix_work_order_created_at`, again 0 scans.
Compiled the dialect-specific expressions against the MySQL dialect to confirm they render
(`avg(timestampdiff(SECOND, created_at, completed_at) / 86400)`, `date(completed_at) <=
due_date`) — MySQL itself wasn't run.

### Manual — dev server driven with `curl`

Seeded two sites, three facilities (built 1965/1998/2012), nine HVAC assets with mixed
conditions, a quarterly PM plan, and 60 work orders with random dates/statuses/costs across
120 days. Logged in as Admin, Technician and School Staff: every view, the year/quarter/
today/custom presets, site + category and status filters, the facility page, the work order
page and the JS asset all returned 200 with the expected sections; the `MO_DASHBOARD` blob
parsed as JSON with all ten charts matching the ten canvases; no nested forms; server log
clean. The insights against that data read correctly, e.g. *"38 open work orders are past
due; Main Building has the most (14)"*, *"28.3% of work orders opened this year were
preventive (PM-generated); 43 were corrective"*, *"9 recurring issues detected this year;
most frequent: "ac not cooling" (similar descriptions) (21 work orders)"*, *"Actual cost came
in under estimates by 22% across 38 work orders with both recorded this year"*; health
scores 35/56/60 for the 1965/1998/2012 buildings.

**Caveat (unchanged):** no visual/JS pass in a real browser — the charts' rendering is
verified only as far as valid data reaching the right canvases.

## Deviations from the plan (and why)

1–5. See the interpretation-calls section above (no per-role focus areas in the plan;
role mapping; undefined KPI definitions; Department → team; no background job yet).
6. **Similar-description matching is exact-after-normalization**, not fuzzy — a token-set
   similarity over N work orders is O(N²) and the plan's non-goals rule out anything that
   smells like inference; identical normalized titles is the defensible, explainable rule.
7. **Facility health's period-based factors follow the dashboard date filter** (inspection
   failures, maintenance cost, recurring problems), while condition/open-overdue/PM/age are
   current state — stated on the page so a score changing with the preset isn't a surprise.

## Known follow-ups (not blockers)

- Cache or precompute the dashboard per (view, filters) once volume warrants it — the plan's
  "background jobs for expensive aggregations"; every query is indexed today, so this is a
  when-needed change, not a correctness one.
- Replace the due-date SLA placeholder with Phase 11's per-priority SLA targets — the
  `sla_compliance` computation is the one line to change.
- Phase 7's `cost_rollup()` and Phase 6's vendor performance still aggregate in Python;
  now that `analytics.py` has the SQL patterns, both could move over if they get slow.
- Browser/visual pass on `mo_dashboard.html` and the two detail-page sections.
- Nothing is committed yet — bootstrap + Phases 1–9 are all in the working tree.
