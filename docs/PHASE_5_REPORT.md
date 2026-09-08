# Phase 5 Report — Inspections

Status: **complete**. Definition of done met — inspection templates, results with
template-to-result mapping enforced, optional failure-triggered work-order generation, and
due/overdue tracking mirroring PM's approach; 22 new tests, 194/194 total, no regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_4_REPORT.md`, `../application/CLAUDE.md`.

## What was built

### Models (`application/models.py`)

| Model | Notes |
|---|---|
| `InspectionTemplate` | admin-defined reusable checklist; `category_id`/`priority_id` (both required) classify any work order a failed item spawns — mirrors `MaintenancePlan`'s pattern |
| `InspectionItem` | the checklist questions (`question`, `sort_order`); deactivating one excludes it from new inspections but keeps past results intact; hard delete blocked once referenced |
| `Inspection` | runs a template against **exactly one** of a Facility, Room, or Asset (`CheckConstraint` — a three-way version of `MaintenancePlan`'s two-way one); **dual-purpose like `MaintenanceSchedule`**: starts `status='Scheduled'` with a `due_date` and no results, becomes `'Completed'` in place the instant results are recorded; `generated_work_order_id` points at any work order a failure spawned |
| `InspectionResult` | one per (inspection, active item at scheduling time); `result` ∈ Pass/Fail/Needs Attention (DB `CheckConstraint`); not append-only like `AssetConditionHistory` — recorded once, all at once, in a single submission |

The `generated_work_order_id` link lives **on `Inspection`**, not on `WorkOrder` — so, unlike
the `maintenance_plan`/`inspection` FK columns Phase 3 explicitly deferred because these
tables didn't exist yet, adding Inspections required **zero changes to the `work_order`
table**. The migration only creates four new tables.

### Business logic (`application/inspections.py`)

- `dashboard_buckets(inspections, today=None)` — identical shape to `pm.py`'s version:
  Overdue (`< today`), Due Today, Due This Week (`today+1..today+7`), Upcoming. Computed
  purely from `due_date` on still-`Scheduled` rows, never a stored flag — same reasoning as
  PM, and the exact same 7-day-boundary test pattern.
- `generate_work_order_for_failures(inspection, user)` — called from inside
  `record_inspection_results()` in the *same transaction* that writes the results and marks
  the inspection Completed (it reads `inspection.results` after `flush()` but before
  `commit()`). Creates **at most one** combined work order per inspection — not one per
  failed item — using the template's category/priority, with a description listing only
  the failed items and their notes. `'Needs Attention'` never triggers generation, only
  `'Fail'` does.

### Routes / access model

Admin-only (matching Priority/Category/MaintenancePlan): `inspection_templates`,
`add_inspection_template`, `edit_inspection_template` (also manages items inline, like
Category/Subcategory), `delete_inspection_template` (blocked while any inspection uses it),
`add_inspection_item`, `delete_inspection_item` (soft-deactivates if referenced by results,
else hard-deletes). Staff-only (Admin/Specialist/Technician, site-scoped for Technicians
like the PM dashboard and work order list): `inspections` (list, filterable by status),
`add_inspection` (schedule — validates exactly one target chosen and that the target's site
is one the scheduler can access), `edit_inspection` (detail: shows the results checklist
form while Scheduled, read-only results once Completed), `record_inspection_results`
(the submission that completes it), `inspection_dashboard`. Admin-only: `delete_inspection`
(only while still Scheduled — a Completed one is historical record and cannot be removed,
matching Facility/Room/Asset's "don't destroy history" principle from earlier phases).

### Templates

`inspection_templates.html`, `add/edit_inspection_template.html` (item table + inline add
form, mirroring `edit_category.html`), `inspections.html`, `add_inspection.html`,
`edit_inspection.html` (checklist radio-button form pre-populated with one entry per active
item, or a read-only results table once completed), `inspection_dashboard.html` (four
buckets, same layout as the PM dashboard). Nav: "Inspections" for staff, "Inspection
Templates" under admin Settings, both menus.

### Migration

`migrations/versions/e7efb8ead9da_add_inspection_tables.py` — four `create_table`s
including both check constraints and the unique `(inspection, item)` constraint. Verified
additive against a simulated pre-Phase-5 baseline (stamp `bc1779acd4f1` → upgrade →
autogenerate reports no drift) — and, as noted above, this migration touches nothing in the
existing `work_order` table.

## What was tested

### Automated — `tests/test_inspections.py` (22 tests), full suite 194 passing

- Template/item CRUD: regular user 403; add; duplicate name rejected; add an item via the
  route; hard-delete an unused item; template delete blocked while any inspection uses it.
- Scheduling: regular user 403; schedule against an asset (asserts `site_id` inherited,
  the other two target columns stay null); choosing two targets rejected; choosing zero
  targets rejected; a still-Scheduled inspection can be deleted, one already Completed
  cannot.
- **Template-to-result mapping** (the plan's own first test requirement): submitting a
  result for every active item creates exactly that many `InspectionResult` rows, correctly
  keyed to their `InspectionItem` ids (not just correctly counted — the exact item→result
  pairing is asserted); omitting a result for one item is rejected with zero rows written
  and the inspection left `Scheduled`; deactivating one item removes it from a newly
  scheduled inspection's checklist entirely; a regular user cannot record results.
- **Failed-item work-order generation** (the plan's second test requirement): a Fail result
  generates a work order carrying the template's category/priority, the correct asset link,
  and a description containing the failed item's note but *not* the passed item's question
  text (proving only failures are listed, not a dump of every item); an all-Pass submission
  generates nothing; unchecking the generate option leaves a real failure unaddressed
  (asserted, not just assumed) with no work order created; a lone `'Needs Attention'` result
  does **not** trigger generation, distinguishing it from `'Fail'`.
- **Due/overdue logic** (the plan's third test requirement): the four buckets split
  correctly against hand-built dates including the exact 7-day boundary; a Scheduled,
  far-overdue inspection appears on the dashboard and disappears the moment it's completed
  (proving buckets are truly live-computed, not cached); regular user 403 on the dashboard;
  a Technician at another site sees neither the schedule nor the inspection detail.
- All 172 previously-passing tests still pass, checked both in normal file order and in a
  deliberately reordered run (`test_inspections.py` → `test_work_orders.py` →
  `test_pm.py` → `test_assets.py`) to catch any repeat of Phase 4's cross-module ID
  fragility — none found.

### Manual — dev server driven with `curl`

Created a "Fire Extinguisher Inspection" template with four items (matching the plan's own
example verbatim), scheduled it against an asset, recorded two Pass and two Fail results
with notes, confirmed the flash message and that the generated work order
(`Inspection Follow-up: Fire Extinguisher Inspection — Asset: SMOKE-EXT-1`) contained only
the two failed questions and their notes — not the two passed ones. Confirmed the dashboard
listed the inspection while overdue-and-Scheduled and stopped listing it once Completed.
No nested `<form>`s on any of the four new detail-style pages; server log clean.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **No separate schedule-generation model or background job**, unlike Phase 4's PM.
   The prompt's own wording is "Inspection due/overdue tracking, similar to PM" — a
   dashboard requirement, not a recurring-generation one; Phase 5, unlike Phase 4, has no
   "background job" bullet in its own list. `Inspection` doubles as both the due-dated
   schedule slot and the completed record, exactly the way `MaintenanceSchedule` needed a
   separate table only because one PM plan can cover many assets — an `Inspection` already
   names its one concrete target directly, so no second table was needed.
2. **`InspectionTemplate.category_id`/`priority_id` are required fields**, not in the
   plan's literal model list. Without them, a failed item would have nothing to classify
   the work order it spawns with — same reasoning `MaintenancePlan` already established
   in Phase 4.
3. **One combined work order per inspection**, not one per failed item. The plan says
   "auto-generate a WorkOrder... when an inspection item fails" (singular, and reused
   verbatim across the whole inspection), and one-per-item would spam the work order list
   for a checklist with several related failures on the same piece of equipment.
4. **Generation is an opt-out checkbox, checked by default**, not a template-level or
   global toggle — the prompt's own phrase is "**Option** to auto-generate," which reads
   as inspector discretion at recording time, not an admin-configured switch.
5. **InspectionTemplate/Item CRUD, an inspection list, and a delete route were added**
   beyond the four named models — same reasoning as every prior phase's precedent
   (AssetType in Phase 2, Priority/Category in Phase 3, MaintenancePlan CRUD in Phase 4):
   a checklist system with no way to define checklists or browse past runs isn't usable.
6. **Migration verified against a simulated baseline, not an empty DB** — same as every
   phase (see `PHASE_0_ARCHITECTURE_ANALYSIS.md` §5).
7. **Not built:** photo attachments on inspection results. PROJECT_PLAN.md's "Photo & QR
   Workflow" section mentions inspections among the entities that should support
   multi-photo upload, but this phase's own prompt doesn't list attachments among its four
   "Implement only" items, unlike Phases 1 and 2 which named the attachment mechanism
   explicitly. Left as a follow-up rather than assumed in scope.

## Known follow-ups (not blockers)

- Photo attachments on `InspectionResult`, per PROJECT_PLAN.md's Photo & QR Workflow
  section — would reuse the same `validate_file_upload` + per-entity-folder pattern as
  every other attachment in this project.
- No reschedule action for a Scheduled inspection (change its due date or inspector after
  creation) — only cancel-and-recreate. Not asked for; noted since PM plans got an
  equivalent gap called out in the Phase 4 report.
- Browser/visual pass on the new templates, in particular the radio-button checklist form.
- Nothing is committed yet — bootstrap + Phases 1–5 are all in the working tree.
