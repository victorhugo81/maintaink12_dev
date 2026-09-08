# Phase 8 Report — Projects & Capital Planning

Status: **complete**. Definition of done met — capital project tracking with cost roll-up,
a seven-factor Asset Risk Score, and a Capital Replacement view ranking every asset; 44 new
tests, 287/287 total, no regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_7_REPORT.md`, `../application/CLAUDE.md`.

## What was built

### Models (`application/models.py`)

| Model | Notes |
|---|---|
| `Project` | Site-scoped. `status` ∈ Planning/Approved/In Progress/On Hold/Completed/Cancelled. Links to `WorkOrder` and `Asset` via nullable `project_id` FKs added to those existing tables; links to `Vendor` via a plain `project_vendor` many-to-many table (no per-row fields needed, so no association-object model) |
| `ProjectTask` | Ordered (`sort_order`) checklist-style task list per project; `status` ∈ Not Started/In Progress/Completed |
| `ProjectCost` | Direct project-level spend (design fees, permits, contingency, etc.) — `cost_type` ∈ Design/Permit/Labor/Material/Contingency/Other. Deliberately separate from Phase 7's `CostRecord`, which caches per-**WorkOrder** cost, not per-project |
| `ProjectDocument` | File attachment on a project, following the existing Facility/Room/Asset attachment pattern |
| `Asset.safety_impact`, `Asset.operational_importance` | New nullable integer columns (0–100), admin-set. Neither is derivable from any existing data, so the Risk Score's two "judgment" factors needed a place to live |
| `Asset.project_id`, `WorkOrder.project_id` | Nullable FKs (`ondelete='SET NULL'`) linking an asset or work order to the capital project that covers it |

### Business logic

- **`application/risk.py`** — `calculate_risk(asset, work_orders=None, today=None)` computes
  all seven PROJECT_PLAN.md risk factors, each a 0–100 contribution:
  - **Condition** — from the asset's latest `AssetConditionHistory` score (inverted: worse
    condition → higher risk); unavailable if no assessment exists yet.
  - **Age** — asset age vs. the asset's own `expected_life_years` if set, else the
    `AssetType`'s; unavailable if neither `install_date` nor an expected life is known.
  - **Failure Frequency** — bucketed from the count of work orders against the asset
    (0→0, 1–2→30, 3–5→60, 6+→100). **Always available** — zero work orders is itself a real
    data point (low risk), not "no data."
  - **Maintenance Cost** — from cumulative actual cost against the asset, preferring
    `CostRecord.total_cost` over `WorkOrder.actual_cost` per work order (same fallback rule
    as Phase 7's cost rollup); unavailable only if there's no cost data of either kind.
  - **Safety Impact**, **Operational Importance** — direct pass-through of the two new
    admin-set `Asset` columns; unavailable when unset (`None`).
  - **Warranty Status** — expired warranty → high risk, active → low, no warranty date → 
    unavailable.

  The overall `score` is the **mean of only the available factors** — a missing factor is
  excluded entirely, never defaulted to 0 or a fabricated midpoint. The return shape
  (`{'score', 'factors': {name: {'value', 'available'}}, 'factor_count'}`) keeps every
  factor's availability visible so the Capital Replacement view can show its work rather
  than a single opaque number.
- **`application/projects.py`** — `project_cost_rollup(project)` returns direct
  `ProjectCost` spend, linked-work-order actual cost (same CostRecord/actual_cost fallback),
  their combined total, and the linked work orders' summed estimated cost — the figures the
  project detail page's budget-variance panel is built from.

### Two design decisions made and documented rather than guessed silently

1. **A `-1` sentinel for "Not Assessed" on Safety Impact / Operational Importance.** The
   plan gives these fields a 0–100 range with no stated way to distinguish "assessed as zero
   impact" from "never assessed." Caught before testing while writing `AssetRiskFieldsForm`:
   added a `-1` ("-- Not Assessed --") choice that the route converts to Python `None`,
   keeping the risk calculation's "excluded means excluded" contract intact for these two
   factors too.
2. **WorkOrder/Asset linked to Project via a simple nullable FK, not many-to-many.** The
   plan's own wording ("groups related work orders and assets") doesn't require an asset or
   work order to belong to more than one capital project at once, and a project's asset/
   work-order membership is naturally exclusive in practice (a roof replacement project
   covers a roof, not a roof shared across two capital projects). Vendor is different — the
   same vendor can legitimately serve multiple concurrent projects — so it kept the
   many-to-many table.

### Routes / access model

Project CRUD (`projects`, `add_project`, `edit_project`, `delete_project`) is admin-only,
matching Vendor and MaintenancePlan management. Project sub-resources (tasks, costs, vendor
links, documents) are reached from the project edit page and gated the same way. "Delete" a
project sets its status to `Cancelled` rather than removing the row — same pattern as every
other entity with historical references (soft-delete precedent from Phase 1). `Asset Risk
Score` display and the `Capital Replacement` list are staff-viewable (Admin/Specialist/
Technician), site-scoped for Technicians, matching the Vendor Performance / Cost Rollup
precedent; only Admins can edit an asset's risk fields (`update_asset_risk_fields`).

### Templates

`edit_asset.html` gained a "Risk Score" section (every factor's value and availability
shown, never just the aggregate number) plus the `AssetRiskFieldsForm` and a capital-project
link; `add_asset.html` and `includes/work_order_fields.html` gained a `project_id` field.
New: `projects.html` (list), `add_project.html`, `edit_project.html` (sidebar cost-rollup
summary with budget variance, sticky section nav, one non-nested `<form>` per section:
Details/Tasks/Work Orders/Assets/Vendors/Costs/Documents), `capital_replacement.html`
(assets ranked by risk score, every contributing factor its own column). Nav: "Capital
Projects" and "Capital Replacement" for staff, both menus.

### Migration

`migrations/versions/cf427a9e2830_add_project_tables_and_asset_work_order_.py` — four
`create_table`s (Project, ProjectTask, ProjectCost, ProjectDocument, project_vendor) plus
new columns on two *existing* tables (`asset.project_id`/`safety_impact`/
`operational_importance`, `work_order.project_id`) — the second time this project has had to
alter an existing table (see Fixed below).

## A real bug found and fixed: missing import

`add_project` referenced `PROJECT_STATUSES` in a template context dict, but the constant had
never been added to `routes.py`'s models import line — raised `NameError` on first manual
test of the route. Fixed by adding `PROJECT_STATUSES` to the existing `from .models import
...` line alongside `Project, ProjectTask, ProjectCost, ProjectDocument`. Caught immediately
by the first automated test that exercised the route, before any manual testing began.

A design flaw was also caught and fixed before testing rather than after: `add_project`
originally read `site_id` via a raw `request.form.get('site_id', type=int)` guarded by dead
conditional logic left over from copying the pattern used where `_visible_site_ids()`
actually varies — here `is_admin()` gating means it's always `None`. Simplified by adding a
proper `site_id = SelectField(...)` to `ProjectForm` instead.

## What was tested

### Automated — `tests/test_projects_and_risk.py` (44 tests), full suite 287 passing

- Every risk factor tested individually: Condition (present/absent); Age (asset-level
  `expected_life_years` override of the AssetType's, absent when neither install_date nor
  any expected life is known); Failure Frequency across all four buckets (0, 1–2, 3–5, 6+)
  and confirmed **always available** even at zero; Maintenance Cost (CostRecord preferred
  over `actual_cost`, absent with no cost data); Safety Impact / Operational Importance
  (pass-through and "no data" cases); Warranty (expired, active, no warranty date).
- Full seven-factor aggregation against a hand-computed exact expected score, and a
  partial-data case confirming the average is taken only over available factors (with an
  explicit `factor_count` check — Failure Frequency's always-available rule means a "no
  other data" asset still has `factor_count == 3`, not 2).
- Capital Replacement route: access control (403 for regular users), correct sort order by
  descending risk score.
- Risk-field update route: setting values, and the `-1` sentinel correctly clearing a field
  back to `None`.
- Project CRUD: create/edit, soft "delete" (status → Cancelled, row persists), site-scoping.
- Asset-to-project and work-order-to-project linking via their respective forms.
- Cost rollup: direct `ProjectCost` + work-order cost combine correctly; falls back to
  `WorkOrder.actual_cost` when no CostRecord exists; a project with no costs at all rolls up
  to zero without error.
- Vendor/task association routes (add/remove vendor, add/toggle/delete task).
- Regression: existing ticket, asset, and work order routes still load.

### Manual — dev server driven with `curl`

Created a project with a $25,000 budget, linked an asset and a work order to it, added a
$3,000 direct `ProjectCost` entry, and logged $5,200 of cost against the linked work order —
confirmed the project detail page showed $8,200 total actual cost ($3,000 + $5,200) against
the $25,000 budget, a -$16,800 variance rendered in green (under budget). Set the asset's
Safety Impact/Operational Importance fields and confirmed the Risk Score section recomputed
and displayed all seven factors with correct availability. Confirmed the Capital Replacement
list ranked assets by descending score.

Hit one self-inflicted snag along the way: a `curl` POST to `edit_asset` to link the asset to
the project omitted the `is_active=y` checkbox field, silently deactivating the asset per the
same WTForms checkbox semantics documented since Phase 1 — this then caused a subsequent
`add_work_order` call to reject the asset with "Not a valid choice." Diagnosed with a small
`app.test_request_context()` + `flask_login.login_user()` script to inspect `form.errors`
directly, rather than guessing from the HTTP response; fixed by reactivating the asset via a
direct DB write and rerunning the sequence.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **`-1` sentinel for "Not Assessed" on Safety Impact / Operational Importance** — see the
   design-decision section above.
2. **WorkOrder/Asset linked to Project via nullable FK, not many-to-many; Vendor via
   many-to-many** — see the design-decision section above.
3. **Project CRUD and sub-resources (tasks, costs, vendor links, documents) beyond the
   literal "Project, ProjectTask, ProjectCost models" list** — same category of call as
   every prior phase's reference-data CRUD (Priority/Category in Phase 3, MaintenancePlan in
   Phase 4, InspectionTemplate in Phase 5, Vendor in Phase 6): a model with no way to create
   or manage it isn't usable.
4. **Migration verified against a simulated baseline covering two altered tables at once** —
   generalized the Phase 6 "compile the real `CREATE TABLE` DDL, strip just the new
   column's line" technique to strip columns across both `asset` and `work_order` in one
   baseline build, since this phase alters two existing tables instead of one.

## Known follow-ups (not blockers)

- Risk Score and Capital Replacement rank in Python over a loaded asset/work-order list
  rather than a SQL-side computation — same tradeoff already flagged for Phase 6/7's
  aggregations in `PHASE_0_ARCHITECTURE_ANALYSIS.md` §8; fine at current scale.
- Browser/visual pass on the new templates, especially `edit_project.html`'s multi-section
  layout.
- No capital planning budget/forecast rollup across *multiple* projects (single-project cost
  rollup only) — not asked for in this phase's scope.
- Nothing is committed yet — bootstrap + Phases 1–8 are all in the working tree.
