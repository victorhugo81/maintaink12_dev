# Phase 3 Report — Work Orders

Status: **complete**. Definition of done met — WorkOrder model/migration/CRUD, requester
submission form, assignment, enforced status workflow; 27 new tests, 155/155 total, legacy
ticket routes untouched and still passing.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md` (Recommendation 2 is
what this phase implements), `PHASE_1_REPORT.md`, `PHASE_2_REPORT.md`,
`../application/CLAUDE.md`.

## What was built

### WorkOrder is a new model, coexisting with Ticket

Exactly as recommended in Phase 0: `Ticket` and its routes are untouched; `WorkOrder` is a
separate table built with Ticket's *conventions* (child comment/attachment tables, a
central per-record authorization helper, the same three access tiers, the same
notification shape) but its own schema.

### Models (`application/models.py`)

| Model | Notes |
|---|---|
| `Priority` | admin-editable: `name`, `description`, `sort_order` (1 = most urgent), `color` (badge), `is_active` |
| `Category` / `Subcategory` | admin-editable; subcategories managed inline on the category page; unique `(category, name)` |
| `WorkOrder` | `wo_number` ("WO-000123", assigned from id after flush), required `site_id`, optional `facility_id`/`room_id`/`asset_id` (`SET NULL`), `title`, `description`, `source` (Request / Manual / PM / Inspection), `status`, `priority_id`, `category_id`, optional `subcategory_id`, **nullable `requester_id`**, `assigned_to_id`, `assigned_team`, `created_by_id`, `scheduled_date`, `due_date`, `started_at`, `completed_at`, `closed_at`, `resolution`, `estimated_cost`, `actual_cost`, timestamps |
| `WorkOrderComment`, `WorkOrderAttachment` | mirror `Ticket_content` / `Ticket_attachment` |
| `WorkOrderStatusHistory` | every status change incl. the creating row: `from_status`, `to_status`, `changed_at`, `changed_by_id`, `note` |

Defaults for priorities (the plan's five) and categories (the plan's twenty-four) live in
`application/reference_data.py`; `seed_reference_data()` is idempotent and is called by
both `installation/seed_data.py` and the test fixtures.

### Status workflow (`application/workflow.py`)

The eleven plan statuses, an explicit `TRANSITIONS` matrix, `can_transition()`,
`allowed_transitions()`, and `apply_transition(wo, to_status, user, note, completed_at,
resolution)` — the only sanctioned way to change a status. It:

- rejects transitions not in the matrix (`WorkflowError`);
- **Completed**: requires a completion date (defaults to now; future dates rejected);
- **Closed**: requires a non-empty resolution; also stamps `completed_at` if missing;
- **In Progress**: stamps `started_at` on first entry; clears terminal stamps on reopen;
- **Cancelled**: stamps `closed_at`; Cancelled → New and Closed → In Progress are the
  reopen paths;
- appends a `WorkOrderStatusHistory` row every time.

Assigning a technician to a `New` work order auto-transitions it to `Assigned` through the
same function.

### Routes (`application/routes.py`, "Work Orders (M&O)" section)

Reference data (admin): `priorities`/`add_priority`/`edit_priority`/`delete_priority`,
`categories`/`add_category`/`edit_category`/`delete_category`,
`add_subcategory`/`delete_subcategory` (in-use rows are deactivated, never deleted).

Work orders: `work_orders` (list; filters for status — default "All Open" — priority,
category, site, assignee incl. me/unassigned, source, and search over number/title; sorted
by priority then newest; eager-loads relations), `request_work_order` (requester flow),
`add_work_order` (staff, source Manual), `edit_work_order` (detail + staff edit),
`change_work_order_status`, `add_work_order_comment` (rate-limited like ticket comments),
`download_work_order_attachment`, `delete_work_order_attachment`.

Helpers: `can_access_work_order`, `can_manage_work_order`, `is_staff`,
`_validate_work_order_links` (room ⊂ facility, asset ⊂ facility, subcategory ⊂ category,
site derived from facility), choice builders that keep a work order's current (possibly
deactivated) links selectable.

### Access model

Identical shape to tickets: Admin/Specialist see and manage all; Technicians see and manage
their own site; everyone else sees only work orders they requested or are assigned to and
can comment but not edit or change status. Requesters can only choose facilities at their
own site.

### Notifications

`send_work_order_notification(event, wo, ...)` in `email_utils.py` mirrors the ticket
version for `created`/`assigned` (→ assignee), `status` (→ requester), `comment`
(→ requester + assignee, minus the commenter). Work orders without a requester simply
have fewer recipients. Same best-effort semantics: skipped if SMTP isn't configured,
failures logged and swallowed.

### Templates

`work_orders.html`, `request_work_order.html` (large controls, `capture="environment"` on
the photo input, single-column on small screens), `add_work_order.html`,
`edit_work_order.html` (sidebar with facts + status-change panel; main column with details
form, comments, attachments, status history — four separate non-nested forms),
`includes/work_order_fields.html` (shared staff fields, readonly for requesters),
`priorities.html`, `add/edit_priority.html`, `categories.html`, `add/edit_category.html`.
Nav: a new "Maintenance" section (Work Orders, New Request) for everyone; Priorities and
Categories under admin Settings; mobile menu mirrored.

### Migration

`migrations/versions/cb141050cfff_add_work_order_priority_category_tables.py` — seven
`create_table`s. Verified additive against a simulated pre-Phase-3 baseline (stamp
`f11aade72780` → upgrade → autogenerate reports no drift).

## What was tested

### Automated — `tests/test_work_orders.py` (27 tests), full suite 155 passing

- Reference data seeded (5 priorities, 24 categories); admin-only pages 403 for regular
  users; add priority + duplicate rejected; add subcategory.
- Requester flow: form loads with own-site facilities; submit creates a `Request` work order
  with correct site/facility/room/requester, `WO-nnnnnn` number, `New` status and one
  history row; room from another facility rejected; requester sees it in list and detail
  but gets no status panel; requester POST to edit → 403; requester can comment; a
  *different* regular user gets 403 and doesn't see it in the list.
- Staff flow: regular user 403 on `add_work_order`; admin creates a Manual work order with
  no requester and an assignee → status `Assigned`, history `['New', 'Assigned']`; a
  Technician at another site gets 403 on detail and on status change; assigning a tech
  through the edit form moves `New` → `Assigned`.
- Workflow: matrix spot-checks (incl. no self-transitions, reopen paths); unknown status
  raises; invalid `Assigned → Closed` rejected via the route; `In Progress` stamps
  `started_at` and records the note; `Completed` with a future date rejected, with a valid
  date stored; `Closed` without resolution rejected, with resolution stamps `closed_at`;
  the full history trail reads `New → Assigned → In Progress → Completed → Closed`; reopen
  clears the terminal stamps; list status filters; in-use priority can't be deleted.
- Legacy: `/tickets` and `/add_ticket` still load; all 128 pre-existing tests unchanged.

### Manual — dev server driven with `curl`

Seeded, created a facility/room, a Teacher and a Technician. Teacher: submitted a request,
saw it, got 403 on edit. Admin: assigned the tech via the edit form; the status panel
offered exactly `Assigned`'s allowed transitions; `Closed` from `Assigned` was refused;
In Progress → Completed → Close refused without resolution (flash shown) → Closed with
resolution → comment; final page shows Closed badge, resolution, note and comment.
List, staff form, category page, priorities page all 200. No nested forms on the three
detail pages checked; server log clean.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **`WorkOrderStatusHistory` was added** — not in the plan's model list, but "valid
   transitions enforced" needs somewhere to record them, and Phase 9's Avg Response /
   Resolution Time KPIs can't be computed without it.
2. **Future FK fields (maintenance plan, inspection, vendor) were not added yet.** The
   prompt says to leave them nullable "for now", but their target tables don't exist and a
   FK can't point at a table that isn't there. They'll be added in Phases 4–6 as additive
   `ALTER TABLE ... ADD COLUMN` migrations, which is equally non-destructive.
3. **"Team" is a free-text field**, not a Team model — none exists and the plan doesn't
   define one; Phase 4 ("assigned team" on MaintenancePlan) is the natural point to decide.
4. **No auto-assignment on request** (tickets auto-assign the first tech at the site).
   Work orders start `New` and unassigned so "Unassigned" is a meaningful KPI and M&O
   managers triage explicitly.
5. **No delete route**: cancel instead. Deleting would orphan the history the plan says to
   keep.
6. **Requesters pick the priority ("urgency") themselves**, unrestricted. Managers can
   change it on the detail page; a future SLA phase may want to distinguish "requested" vs
   "assigned" priority.
7. **Comments use a plain form post**, not the ticket page's AJAX flow — simpler, works
   without JS on a phone, and the page reloads anyway.
8. **Migration verified against a simulated baseline, not an empty DB** — same as every
   phase (see `PHASE_0_ARCHITECTURE_ANALYSIS.md` §5).

## Known follow-ups (not blockers)

- Browser/visual pass; the requester form in particular deserves a real phone check.
- Facility → room/asset and category → subcategory selects are not filtered client-side;
  server-side validation catches mismatches (same as Phases 1–2).
- `AssetType.category` (free text, Phase 2) should become a FK to `Category` now that it
  exists — a small follow-up migration.
- Nothing is committed yet — bootstrap + Phases 1–3 are all in the working tree.
