# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Maintaink12 started as a copy of the [AssistItK12](https://github.com/victorhugo81/assistitk12)
codebase (see docs/PROJECT_PLAN.md) — this changelog covers Maintaink12's own history from
that point forward, not AssistItK12's.

## [0.9.0] - 2026-09-08

### Added
- M&O Dashboard (`/dashboard`, `application/analytics.py`) with four role views —
  Executive, M&O Manager (Admins/Specialists switch between the two), Technician (pinned to
  the user's own work and site) and School Staff (pinned to the user's own requests and
  site). Every KPI comes from PROJECT_PLAN.md's Canonical KPI List; the ones the plan names
  without defining (Backlog, Completion Rate, SLA Compliance %, Repeat Work %, Assets
  Requiring Attention) have their definition documented on the computing function. The
  legacy ticket dashboard at `/` is untouched.
- Facility Health Score (0–100) from the plan's seven factors, each shown with its value
  and the data behind it — on the dashboard for every facility in scope and on each
  facility's detail page. Same contract as the Phase 8 risk score: a factor with no data
  is excluded from the mean, never counted as zero.
- Recurring Issue Detection: work orders repeating (3+) on the same asset, the same
  room/facility within one category, or with the same normalized description; broader
  groups that add nothing over a more specific one are dropped. Listed on the dashboard
  and linked as a "Recurring Issue" panel on each affected work order's detail page.
- Operational Insights: rule-based statements (volume vs previous period, a facility vs
  the average for the top category, overdue/unassigned counts, PM share, top-category
  share, condition, recurring, estimate variance, due-date compliance, resolution-time
  trend) — each emitted only when the query results support it, quoting those numbers.
- Charts (Chart.js 4, the bundle the ticket dashboard already ships): work orders by
  status/priority/facility/category/technician/month, cost over time, PM vs corrective,
  condition distribution, response/completion trends.
- Date presets (today/week/month/quarter/year/custom) and filter-by (site, facility,
  department/team, technician, category, priority, status) applied through one shared
  filter dict to every widget.
- Index on `work_order.completed_at` (migration `d919f88115eb`) for "completed in period"
  queries. Dashboard aggregates run as SQL `GROUP BY`/`SUM(CASE)` and were verified with
  `EXPLAIN QUERY PLAN` to hit an index on every `work_order` access (filtered and
  district-wide); the only row-level scan (recurring detection) is capped at 5,000 rows.

## [0.8.0] - 2026-09-08

### Added
- Project/ProjectTask/ProjectCost/ProjectDocument models. A project groups WorkOrders and
  Assets via nullable `project_id` columns added to those existing tables (the second
  Phase-6-style dialect-aware SQLite migration — see Fixed below) and Vendors via a plain
  many-to-many table. ProjectCost captures direct project-level spend (design fees,
  permits) separate from Phase 7's per-WorkOrder CostRecord cache.
- Project cost roll-up (`application/projects.py`): direct ProjectCost entries plus every
  linked WorkOrder's cost (preferring CostRecord.total_cost, falling back to
  WorkOrder.actual_cost), against the linked work orders' summed estimated cost.
- Asset Risk Score (`application/risk.py`): all seven PROJECT_PLAN.md factors (condition,
  age, failure frequency, maintenance cost, safety impact, operational importance,
  warranty status), each a 0-100 contribution, averaged over whichever factors have real
  data — a missing factor is excluded, never defaulted to 0 or a fabricated midpoint. Two
  factors (safety impact, operational importance) aren't derivable from any existing data,
  so `Asset` gained two new admin-set columns for them (also added via the dialect-aware
  migration).
- Capital Replacement view: every asset ranked by risk score, every contributing factor
  shown in full (never just the number), staff-viewable and site-scoped.

### Fixed
- Applied the Phase 6 dialect-aware migration pattern to two more columns-on-existing-
  tables this time (`asset.project_id`/`safety_impact`/`operational_importance` and
  `work_order.project_id`) — same SQLite batch-recreate-needs-named-constraints limitation,
  verified not to affect MySQL. Building an accurate pre-migration baseline for two altered
  tables at once required generalizing the "compile the real CREATE TABLE DDL, strip just
  the new columns' lines" technique from Phase 6 to run over both tables.

## [0.7.0] - 2026-09-08

### Added
- WorkOrderMaterial — not in the plan's numbered model list for this phase, but named as
  the source of "material cost" in its own cost-record description and never built in
  Phase 3; added now since CostRecord's material cost has nowhere else to come from.
- WorkOrderLabor (technician, optional start/end time with hours auto-computed when both
  are given, labor type, hourly rate, notes) and CostRecord — a per-WorkOrder cost-summary
  cache (labor/material costs derived and kept in sync via `application/costs.py`'s
  `refresh_cost_record()`; vendor/other costs entered directly).
- Cost rollups across nine dimensions (Work Order, Facility, School/Site, Asset, Category,
  Department/Team, Vendor, Month, Year) — Estimated vs Actual, with Actual preferring the
  CostRecord total and falling back to `WorkOrder.actual_cost` for untracked work orders.
  "Department" has no backing model anywhere in this schema; mapped to
  `WorkOrder.assigned_team`, the closest existing concept.
- Technician workload view (open/in-progress/overdue/due-today/due-this-week counts per
  technician), site-scoped like the PM/Inspection dashboards.
- Labor, Materials, and Cost Summary sections on the work order detail page.

## [0.6.0] - 2026-09-07

### Added
- Vendor model (contact info, contract dates, insurance/license expiration) and an optional
  `WorkOrder.vendor_id` link — the first migration in this project to ALTER an existing
  table rather than only create new ones.
- Vendor performance view: one aggregate table across all active vendors — work order
  counts (total/open), total actual cost, average completion time, and contract/insurance/
  license expiration status (expired/expiring within 30 days/ok). Staff-viewable, admin
  manages the vendor records themselves.
- `application/vendors.py`: pure, directly-tested aggregation (`compute_vendor_stats`) and
  expiration-window logic (`expiration_status`).

### Fixed
- The autogenerated migration for `WorkOrder.vendor_id` used `batch_alter_table` to add the
  column's foreign key, which on SQLite requires recreating the whole `work_order` table —
  and that recreate fails because none of the table's existing FK constraints have explicit
  names. Verified this is SQLite-specific (MySQL, the real production target, handles a
  plain `ADD COLUMN ... ADD CONSTRAINT` natively, no recreate needed) and made the migration
  dialect-aware: the DB-level FK constraint is added everywhere except SQLite, which gets
  the column and the ORM-level relationship only. See `application/CLAUDE.md`.

## [0.5.0] - 2026-09-07

### Added
- InspectionTemplate (admin-defined checklist, carrying a required category/priority used
  to classify any work order it spawns) and InspectionItem (the checklist questions) in
  `application/models.py`. Additive migration `e7efb8ead9da_add_inspection_tables`.
- Inspection and InspectionResult: an Inspection runs a template against exactly one of a
  Facility, Room, or Asset (DB CheckConstraint). It's dual-purpose like Phase 4's
  MaintenanceSchedule — starts "Scheduled" with a due date and no results, becomes
  "Completed" in place the moment results are recorded. Recording requires a Pass/Fail/
  Needs Attention result for every active checklist item in one submission.
- Optional automatic work-order generation (source=Inspection) for any Fail result,
  combining every failed item from one inspection into a single work order classified by
  the template's category/priority — a checkbox on the results form, checked by default.
  The link is stored on Inspection (`generated_work_order_id`), so no column was added to
  the existing `work_order` table.
- Inspection due/overdue dashboard (Upcoming/Due Today/Due This Week/Overdue), computed
  purely from due_date on still-Scheduled rows — the same approach as the Phase 4 PM
  dashboard, never a stored "Overdue" flag. Staff-only, site-scoped for Technicians.
- InspectionTemplate/Item CRUD, admin-only like Priority/Category/MaintenancePlan.

## [0.4.0] - 2026-09-07

### Added
- MaintenancePlan (tied to exactly one of a specific Asset or an AssetType, frequency
  Daily/Weekly/Monthly/Quarterly/Semiannual/Annual/Custom, assignment) and
  MaintenanceSchedule (the per-asset "next due" tracker — resolves an asset-type plan down
  to one row per matching asset) in `application/models.py`. Additive migration
  `bc1779acd4f1_add_maintenance_plan_and_schedule_tables`.
- `application/pm.py`: due-work-order generation, callable directly (tests, an admin "run
  now" button) or via the new daily APScheduler job (`run_pm_schedule_generation` in
  scheduled_jobs.py, registered unconditionally in main.py). Generates a WorkOrder
  (source=PM) for every due schedule and advances `next_due_date` past "today" in the same
  transaction — the guarantee against duplicate work orders for one due cycle. An overdue
  schedule still gets exactly one work order and fast-forwards its cadence past today,
  rather than backfilling one work order per missed cycle.
- PM dashboard (Upcoming / Due Today / Due This Week / Overdue), staff-only and site-scoped
  for Technicians like the work order list. Admin-only "Run PM Generation Now" button.
- MaintenancePlan CRUD, admin-only like Priority/Category.

### Fixed
- `tests/test_work_orders.py` assumed the first WorkOrder created in the whole test session
  always gets id 1 / number "WO-000001" — true only when no other test module created a
  WorkOrder first. Adding `tests/test_pm.py` (which sorts alphabetically before
  test_work_orders.py and creates its own work orders) broke that assumption. Fixed by
  looking up the test's work order by its title instead of an assumed number.

## [0.3.0] - 2026-09-07

### Added
- WorkOrder model (plus WorkOrderComment, WorkOrderAttachment, WorkOrderStatusHistory) as a
  new model alongside the legacy Ticket, per the Phase 0 recommendation. Requester is
  optional (`source` = Request / Manual / PM / Inspection); optional links to Facility, Room
  and Asset; site-scoped. Additive migration
  `cb141050cfff_add_work_order_priority_category_tables`.
- Admin-editable Priority, Category and Subcategory reference data, seeded from
  PROJECT_PLAN.md via `application/reference_data.py` (used by seed_data.py and tests).
- Status workflow in `application/workflow.py`: the 11 plan statuses, an explicit
  transition matrix, and `apply_transition()` which enforces the data-quality rules
  (Completed needs a completion date not in the future; Closed needs a resolution), stamps
  started/completed/closed timestamps, and writes a WorkOrderStatusHistory row.
- Requester-facing "New Request" form (what / where / type / urgency / description / photo),
  staff "New Work Order" form, work order list with filters, and a detail page with
  status-change panel, comments, attachments and status history.
- Assignment to a technician (or free-text team); assigning a New work order moves it to
  Assigned. Email notifications for created/assigned/status/comment events mirror the
  ticket notifications.

## [0.2.0] - 2026-09-07

### Added
- AssetType (admin reference data) and Asset models; assets belong to a Facility and
  optionally a Room, carry a unique asset tag, and are site-scoped like Rooms. Additive
  migration `f11aade72780_add_asset_and_asset_condition_tables`.
- AssetConditionHistory: append-only condition assessments (0-100 score mapped to
  Excellent/Good/Fair/Poor/Critical). ORM listeners raise `ConditionHistoryImmutableError`
  on any update or delete, so history can't be rewritten even by application code.
  Asset caches the newest entry's score/label for list filtering.
- Asset CRUD, asset detail page with condition history and a "record assessment" form
  (Admins, Specialists and Technicians can record; site-scoped), asset attachments,
  optional photo per assessment linked to that history entry.
- QR code PNG per asset (`/asset_qr/<id>.png`) encoding the asset detail URL, shown on
  the detail page with a download link. Adds the `qrcode[pil]` dependency.

### Fixed
- `edit_facility.html` nested the add-floor form inside the main facility form (invalid
  HTML; in a browser "Add Floor" would have submitted the facility edit instead). Floors
  section now sits outside the main form.

## [0.1.0] - 2026-09-07

### Added
- Facility, Floor, and Room models, each scoped to a Site, with an additive migration
  (`7d40fabd6b8b_add_facility_floor_room_tables`).
- Facility and Room CRUD: list/detail pages open to any authenticated user (site-scoped
  via `can_access_site`), create/edit/delete gated to Admins (matching Site management).
- Inline floor management from the Facility detail page (add/delete, blocked while a
  floor still has rooms assigned).
- Photo/document attachment support on Facility and Room, reusing the ticket attachment
  validation/upload pattern (`FacilityAttachment`, `RoomAttachment`).
- Soft delete (`is_active`) for Facility and Room so later phases (assets, work orders,
  inspections) can keep history against a retired facility/room.
