# Maintaink12 — Project Plan

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
