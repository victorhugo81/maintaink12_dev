# Maintaink12 — Project Plan & Phase Prompts

This file has two parts:

1. **PROJECT PLAN** — the persistent vision doc. Save this in the repo (e.g. `docs/PROJECT_PLAN.md`) so every future prompt can reference it instead of re-explaining the whole system.
2. **PHASE PROMPTS** — one self-contained prompt per phase. Copy just one phase's block into your coding agent at a time. Each phase prompt references the plan doc rather than repeating it.

Do not paste the whole file into the agent in one message — that defeats the point of splitting it.

---

# PART 1 — PROJECT PLAN
*(save as `docs/PROJECT_PLAN.md` in the repo)*

## Vision

Evolve **AssistItK12** (Flask + Bootstrap + MySQL K–12 ticketing platform: users, roles, sites, tickets, assignments, attachments, notifications, reporting, dashboards) into **Maintaink12**, a lightweight CMMS/work-order management system for K–12 Maintenance & Operations (M&O) departments.

Build on the existing codebase at https://github.com/victorhugo81/assistitk12. Preserve its architecture, auth, ticketing engine, security practices, DB conventions, migrations, notifications, site management, reporting, and UI patterns wherever practical. Do not start a separate application from scratch, and do not simply rename "IT Ticket" to "Maintenance Ticket" — the request/work-order model needs to actually change shape (see below).

The system should let a school district M&O department answer three questions:
1. **What needs to be done?** — Work Orders / Requests
2. **What do we have?** — Facilities / Rooms / Assets
3. **How well are we operating?** — Analytics / KPIs / Costs / Performance / Condition

## Core Design Principle

Model the flow as:

`Request → Work Order → Assignment → Work → Inspection → Completion → Cost → Asset History → Analytics`

A work order must be able to exist **independently of a user complaint** — preventive maintenance, scheduled inspections, emergency repairs, seasonal maintenance, safety inspections, facility projects, asset replacement, and contractor work must all be first-class work order sources, not just "ticket types."

## Non-Goals (v1)

Explicit scope boundaries — don't gold-plate these in early phases:
- No native mobile app; mobile-first **responsive web** only.
- No real barcode/QR scanning hardware integration — QR codes just encode a URL to the asset/room/facility page, resolved by any phone camera.
- No multi-district / multi-tenant support — single district, multiple sites (matches AssistItK12's existing site model).
- No payroll or HR integration for labor tracking — labor hours/cost are recorded for reporting only.
- No accounting-system integration for cost tracking — cost fields are recorded manually or via CSV import, not synced to an ERP.
- No AI/ML-based predictive failure modeling — "Intelligent Insights" (below) are rule-based aggregations over real data only, never fabricated or inferred beyond what's in the DB.
- PDF/Excel export is "nice to have per report," not a blocking requirement for any phase — CSV export is the baseline.

## Data Model (target — extend, don't duplicate)

Hierarchy: `District → Site/Campus → Facility/Building → Floor → Room/Area → Asset`

New models to add alongside existing AssistItK12 models (Users, Roles, Sites, Tickets, Attachments, Notifications):

`Facility, Floor, Room, Asset, AssetType, AssetCondition, AssetConditionHistory, WorkOrder, WorkOrderComment, WorkOrderAttachment, WorkOrderLabor, WorkOrderMaterial, MaintenancePlan, MaintenanceSchedule, Inspection, InspectionTemplate, InspectionItem, InspectionResult, Vendor, Project, ProjectTask, ProjectCost, SLA, CostRecord, FacilityDocument, AssetDocument, RoomDocument`

Every relevant record (User, Facility, Room, Asset, WorkOrder, Inspection, VendorWork, Project) must carry a site relationship, reusing AssistItK12's existing site scoping — district admins see everything, site-level users see only their authorized sites.

Use proper foreign keys, relationships, indexes, constraints, timestamps, audit fields, and soft deletion where appropriate. Maintain Alembic/Flask-Migrate compatibility — **additive migrations only**; assume production data exists in `tickets`, `users`, `sites` and must not be destructively altered.

## Canonical KPI List
*(single source of truth — every dashboard/report below pulls from this list rather than redefining its own)*

**Work Order KPIs:** Total Open, New Requests, Unassigned, In Progress, Waiting, Completed, Overdue, Critical, Emergency, Avg Response Time, Avg Resolution Time, Completion Rate, SLA Compliance %, Backlog, Repeat Work %.

**Facility KPIs:** Total Facilities/Buildings/Rooms/Assets, Assets Requiring Attention, Assets in Poor/Critical Condition, Assets Past Expected Life, Open Issues by Facility, Facility Health Score (0–100, factors shown transparently — see below).

**Maintenance KPIs:** PM Due / Overdue, Inspections Due / Overdue, Recurring Issues, Maintenance Cost (labor + material + contractor), Labor Hours, Preventive vs Corrective %.

**Cost KPIs:** Cost per Work Order / Facility / Asset / Category / Vendor / Month / Year; Estimated vs Actual.

## Standard Reference Data

**Priorities** (admin-editable): Emergency (life safety / major damage / critical infra — immediate response), Critical (major operational impact), High (prompt attention), Medium (normal request), Low (routine).

**Work Order Statuses:** New, Assigned, Scheduled, In Progress, Waiting for Parts, Waiting for Vendor, Waiting for Approval, On Hold, Completed, Cancelled, Closed.

**Condition Scale:** Excellent (90–100), Good (75–89), Fair (50–74), Poor (25–49), Critical (0–24). Condition history is append-only — never overwrite a prior assessment.

**Categories** (admin-editable, with subcategories): HVAC, Electrical, Plumbing, Roofing, Doors & Locks, Grounds, Landscaping, Custodial, Pest Control, Fire/Life Safety, Security, Lighting, Flooring, Painting, Carpentry, Furniture, Playground, Kitchen Equipment, Appliances, Irrigation, Parking, Transportation, General Maintenance, Other.

**Roles** (granular permissions, not just role labels): District Administrator, M&O Director, M&O Manager, Supervisor, Technician, Custodian, School Staff, Read-Only Administrator.

## Scored / Calculated Features

- **Facility Health Score (0–100):** from asset condition, open/overdue work orders, recurring problems, PM compliance, inspection failures, facility age, maintenance cost. Contributing factors must be visible to admins — never an opaque black-box number.
- **Asset Risk Score:** from condition, age, failure frequency, maintenance cost, safety impact, operational importance, warranty status. Drives capital replacement prioritization.
- **Recurring Issue Detection:** flag work orders repeating on the same asset/room/facility/category or with similar descriptions; link related work orders under "Recurring Issue."
- **Operational Insights:** short, data-grounded statements only (e.g. "Jefferson Elementary has 34% more HVAC work orders than the district average over the last 90 days"). Never fabricate a claim not backed by a query result.

## Photo & QR Workflow

Work orders, rooms, assets, and inspections all support multi-photo upload (mobile camera capture), timestamped and stored against the relevant record. QR codes on facilities/rooms/assets resolve to that record's page, showing condition, history, open work orders, PM schedule, documents, warranty, and vendor — with a "submit work order from here" action.

## K–12-Specific Considerations

School calendars, summer/break-period maintenance windows, after-hours work, restricted/occupied-area rules, emergency maintenance, school-specific permissions, budget years, capital projects, and board/administrator reporting all need to be schedulable and reportable on — this is not a generic commercial CMMS.

## Security & Data Quality Baseline

Preserve/extend AssistItK12's existing protections: auth, CSRF, SQL injection, XSS, file-upload validation, path traversal, session security, password security, rate limiting, IDOR, privilege escalation. No secrets in source — environment variables only. Enforce data quality at form + DB level (unique asset tags, valid locations required, closed work orders require resolution, completed work orders require completion date, assets/rooms must belong to a facility).

## Performance Baseline

Design for 20+ schools, thousands of rooms, tens of thousands of assets, hundreds of thousands of work orders. Use indexes, pagination, eager-loading to avoid N+1s, and background jobs for expensive aggregations (dashboard KPIs, insights). Never load unbounded record sets into the browser.

---

# PART 2 — PHASE PROMPTS

Each block below is meant to be pasted as its own message/session. Assume the agent has the repo checked out and can read `docs/PROJECT_PLAN.md` (Part 1 above).

---

## Phase 0 — Repository & Architecture Analysis
*(Do this before any other phase, in its own conversation)*

```
You are a senior Flask architect. Do NOT write any code in this phase.

Read docs/PROJECT_PLAN.md for the target vision (Maintaink12, built on AssistItK12).

Inspect the AssistItK12 repository (https://github.com/victorhugo81/assistitk12) and produce a written analysis covering:

1. Current models, their fields, and relationships
2. Current routes/blueprints and how they're organized
3. Current templates and shared UI components (tables, filters, badges, forms)
4. Current services/business-logic layer, if any
5. Current migrations (Alembic/Flask-Migrate) and naming conventions
6. Current auth/authorization mechanism and how roles/permissions are checked
7. Current notification system
8. Current dashboard implementation and how it queries data
9. Current site-scoping model (how a record is tied to a school/site, how site-level users are restricted)
10. Existing tests, and what they cover

Then propose:
- Where the new Facility → Floor → Room → Asset hierarchy fits into the existing model structure
- Whether WorkOrder should be a new model or an extension of the existing Ticket model, with your reasoning
- Which existing conventions (naming, migration style, blueprint structure, template patterns) the new M&O modules should follow to stay consistent

Do not propose a full schema yet — this phase is understanding + a short recommendation, not design. Stop and report back.
```

**Definition of done:** a written architecture summary + a recommendation on WorkOrder vs Ticket, reviewed and approved before Phase 1 starts.

---

## Phase 1 — Facilities, Buildings, and Rooms

```
Reference docs/PROJECT_PLAN.md for the Facility/Floor/Room data model and non-goals.
Reference the Phase 0 architecture analysis for how to fit this into the existing codebase.

Implement only:
1. Facility, Floor, and Room models (fields per PROJECT_PLAN.md section "Data Model"), each scoped to a Site using the existing site relationship
2. Additive Alembic migrations for these tables — do not touch existing tables destructively
3. CRUD routes/views for Facilities and Rooms, following the existing blueprint and template conventions
4. Facility and Room list/detail pages using the existing UI component patterns (tables, filters, cards)
5. Basic photo/document attachment support on Facility and Room, reusing the existing attachment mechanism if one exists

Do not implement Assets, Work Orders, or any dashboard changes yet — that's later phases.

After implementing: run the existing test suite, add tests for the new CRUD + site-scoping behavior, run migrations against a fresh DB, and confirm no existing routes/templates broke. Report what you built, what you tested, and any deviations from the plan.
```

**Definition of done:** Facility/Floor/Room models + migrations + CRUD + tests passing + zero regressions in existing AssistItK12 tests.

---

## Phase 2 — Assets & Asset Condition

```
Reference docs/PROJECT_PLAN.md for Asset fields, the condition scale (Excellent/Good/Fair/Poor/Critical, 0–100 score), and AssetConditionHistory (append-only, never overwritten).

Implement only:
1. Asset and AssetType models, each belonging to a Facility and optionally a Room
2. AssetConditionHistory model (date, condition, score, inspector, reason, notes, photos, recommended action) — historical records must never be overwritten
3. Migrations (additive)
4. CRUD routes/views for Assets, including recording a new condition assessment (which appends to history, doesn't edit it)
5. Asset detail page showing current condition + full condition history over time
6. QR code generation for each asset (encodes a URL to the asset detail page) — no scanning hardware integration needed, just the code + the page it resolves to

Do not implement Work Orders, PM, or Inspections yet.

After implementing: run tests, add coverage for asset CRUD, condition history immutability, and QR URL resolution. Confirm no regressions. Report back.
```

**Definition of done:** Asset + condition history models/migrations/CRUD/QR working, tests passing, no regressions.

---

## Phase 3 — Work Orders (core M&O ticket workflow)

```
Reference docs/PROJECT_PLAN.md for the WorkOrder field list, statuses, and priorities, and the Phase 0 recommendation on WorkOrder vs Ticket.

Implement only:
1. WorkOrder model (and WorkOrderComment, WorkOrderAttachment) per the field list — must support being created independently of a requester complaint (e.g. source = "PM", "Inspection", "Manual", "Request")
2. WorkOrder can optionally link to a Facility, Room, and/or Asset
3. Configurable Priority and Category/Subcategory models (admin-editable, not hardcoded enums)
4. Status workflow per PROJECT_PLAN.md's status list, with valid transitions enforced
5. Requester-facing submission form (the simple mobile-friendly flow: what/where/type/urgency/description/photo/submit), reusing the existing ticket-submission UX where practical
6. Assignment to a technician or team, reusing the existing assignment/notification mechanism
7. Data-quality rules: closed work orders require a resolution; completed work orders require a completion date

Do not implement PM auto-generation, Inspections, Vendors, or Labor tracking yet — those are later phases, even though WorkOrder has fields that will reference them (leave those fields nullable for now).

After implementing: run tests, add coverage for status transitions, requester submission flow, and site-scoping. Confirm no regressions in the existing ticket system if WorkOrder extends/coexists with it. Report back.
```

**Definition of done:** WorkOrder model/migrations/CRUD/submission-form/assignment/status-workflow working, tests passing, no regressions.

---

## Phase 4 — Preventive Maintenance

```
Reference docs/PROJECT_PLAN.md.

Implement only:
1. MaintenancePlan and MaintenanceSchedule models — a plan tied to an Asset (or Asset Type), a frequency (daily/weekly/monthly/quarterly/semiannual/annual/custom interval), and an assigned team
2. A background job (or scheduled task, matching whatever job mechanism the existing app uses, or a simple cron-style script if none exists) that automatically generates a WorkOrder when a schedule comes due, with source = "PM"
3. PM dashboard section: Upcoming / Due Today / Due This Week / Overdue

Do not implement Inspections yet.

After implementing: test that PM generation doesn't create duplicate work orders for the same due cycle, and that overdue detection is correct across date boundaries. Report back.
```

**Definition of done:** PM plans/schedules + auto work-order generation + PM status views working, tests passing.

---

## Phase 5 — Inspections

```
Reference docs/PROJECT_PLAN.md.

Implement only:
1. InspectionTemplate and InspectionItem models (admin-defined checklist questions, e.g. Fire Extinguisher Inspection: Present? Accessible? Pressure OK? Damage? Expiration Date? Tag current?)
2. Inspection and InspectionResult models — an inspection run against a Facility/Room/Asset using a template, producing Pass/Fail/Needs Attention per item
3. Option to auto-generate a WorkOrder (source = "Inspection") when an inspection item fails
4. Inspection due/overdue tracking, similar to PM

After implementing: test template-to-result mapping, failed-item work-order generation, and due/overdue logic. Report back.
```

**Definition of done:** Inspection templates + results + failure-triggered work orders working, tests passing.

---

## Phase 6 — Vendors

```
Reference docs/PROJECT_PLAN.md.

Implement only:
1. Vendor model (contact info, contract dates, insurance/license expiration) and linking Vendors to WorkOrders
2. Vendor performance view: work orders by vendor, cost by vendor, average completion time, open work, contract expiration warnings

Do not implement Inventory tracking — not needed for this project.

After implementing: test vendor-to-work-order linking and vendor cost/performance aggregation. Report back.
```

**Definition of done:** Vendor model/CRUD/linking/performance view working, tests passing.

---

## Phase 7 — Labor & Cost Tracking

```
Reference docs/PROJECT_PLAN.md.

Implement only:
1. WorkOrderLabor model (technician, work order, start/end time, labor hours, labor type, notes)
2. CostRecord model aggregating labor cost + material cost (from WorkOrderMaterial entries recorded directly on the work order, no inventory/stock tracking) + vendor cost + other expenses per WorkOrder
3. Cost rollups by Work Order, Facility, School, Asset, Category, Department, Vendor, Month, Year — Estimated vs Actual
4. Technician workload calculation (open/in-progress/overdue/due-today/due-this-week counts per technician)

After implementing: test labor-hour and cost aggregation correctness across the rollup dimensions. Report back.
```

**Definition of done:** Labor tracking + cost rollups + technician workload calculations working, tests passing.

---

## Phase 8 — Projects & Capital Planning

```
Reference docs/PROJECT_PLAN.md.

Implement only:
1. Project, ProjectTask, and ProjectCost models — a project can group multiple Work Orders, Assets, Vendors, Documents, and Costs, with its own status workflow (Planning/Approved/In Progress/On Hold/Completed/Cancelled)
2. Asset Risk Score calculation per PROJECT_PLAN.md's factor list (condition, age, failure frequency, maintenance cost, safety impact, operational importance, warranty status)
3. Capital Replacement view: assets ranked by risk score, with the contributing factors shown (never an opaque number)

After implementing: test risk-score calculation against known inputs, and project cost roll-up from its linked work orders. Report back.
```

**Definition of done:** Projects + capital planning/risk scoring working, tests passing.

---

## Phase 9 — Dashboards & Analytics

```
Reference docs/PROJECT_PLAN.md's Canonical KPI List — do not invent new KPI definitions, pull from that list.

Implement only:
1. Role-based dashboards: Executive, M&O Manager, Technician, School Staff (per PROJECT_PLAN.md's dashboard focus areas for each role)
2. Facility Health Score calculation, with contributing factors visible
3. Recurring Issue Detection (same asset/room/facility/category or similar description repeating)
4. Operational Insights section — short, data-grounded statements only, generated from real query results, never fabricated
5. Charts: work orders by status/priority/facility/category/technician/month, cost over time, PM vs corrective, condition distribution, response/completion trends — using whatever charting library the existing app already uses if one exists, otherwise pick the lightest option compatible with the stack
6. Date filtering (today/week/month/quarter/year/custom) and filter-by (site/facility/department/technician/category/priority/status) applied consistently across dashboard widgets

After implementing: test KPI calculations against seeded data with known expected values, and confirm dashboard queries are paginated/indexed appropriately for the performance baseline in PROJECT_PLAN.md. Report back.
```

**Definition of done:** role dashboards + health score + recurring-issue detection + insights + charts + filtering working, tests passing, dashboard queries verified against the performance baseline.

---

## Phase 10 — Reports & Exports

```
Reference docs/PROJECT_PLAN.md.

Implement only the report list from PROJECT_PLAN.md's "Canonical KPI List" and section on reporting: Work Order, Open Work Order, Overdue Work Order, Preventive Maintenance, Asset Condition, Facility Condition, Maintenance Cost, Technician Productivity, Vendor Performance, Asset Maintenance History, Capital Replacement, SLA Performance, Recurring Problems.

Each report needs: date filtering, site filtering, CSV export. PDF/Excel export only if it's cheap given the existing stack — otherwise note it as a follow-up, per the non-goals in PROJECT_PLAN.md.

After implementing: test CSV export correctness against known data. Report back.
```

**Definition of done:** all listed reports with filtering + CSV export working, tests passing.

---

## Phase 11 — SLA Rules, Notifications, Global Search, CSV Import

```
Reference docs/PROJECT_PLAN.md.

Implement only:
1. Configurable SLA model (response time targets per priority), SLA deadline calculation, breach detection, and dashboard surfacing of breaches
2. Extend the existing notification system to cover: PM due/overdue, inspection due/failed, vendor contract expiration, asset warranty expiration, SLA warning/breach — with user-configurable preferences, and without over-notifying
3. Global search across work orders, facilities, rooms, assets, vendors, projects, users (by asset tag, serial number, work order number, room, facility, description)
4. CSV import tool for Facilities, Rooms, Assets, Vendors, Users — with a downloadable template, pre-commit validation, and an import report (success/failed/errors/duplicates) that never partially corrupts existing data on failure

After implementing: test SLA breach detection accuracy, notification preference honoring, search relevance on known fixtures, and CSV import validation/rollback-on-error behavior. Report back.
```

**Definition of done:** SLA + notifications + search + CSV import working, tests passing.

---

## Phase 12 — Mobile Optimization & Security Audit

```
Reference docs/PROJECT_PLAN.md's Security & Data Quality Baseline and Non-Goals (responsive web, not native app).

Implement/verify only:
1. Mobile-responsive pass on: today's-work view, work order detail/update, photo upload, QR-code landing pages, labor/parts recording, checklist completion — confirm technicians never need the desktop nav to do these
2. Full security review: auth, authorization/permission checks on every new route, CSRF, SQL injection, XSS, file-upload validation (type/size/secure filename handling), path traversal, session security, rate limiting, IDOR on every new resource (work orders, assets, facilities, vendors, projects), privilege escalation via role/permission checks
3. Audit log covering: work orders, assets, facilities, users, permissions, costs, status changes (who changed what, old value, new value, timestamp)

After implementing: run/extend the full regression test suite covering every phase, do a final pass confirming no existing AssistItK12 functionality was broken, and produce a summary of: what changed, what was added, what was preserved, schema changes, new dependencies, security improvements, and migration instructions for an existing AssistItK12 installation moving to Maintaink12.
```

**Definition of done:** mobile pass complete, security review complete with audit log in place, full regression suite green, final migration/changelog summary delivered.
