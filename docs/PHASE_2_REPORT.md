# Phase 2 Report — Assets & Asset Condition

Status: **complete**. Definition of done met — Asset + condition history models, additive
migration, CRUD, QR codes, 24 new tests passing, 128/128 total, zero regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`,
`../application/CLAUDE.md`.

## What was built

### Models (`application/models.py`)

| Model | Fields | Notes |
|---|---|---|
| `AssetType` | `name` (unique), `category`, `expected_life_years`, `description`, `is_active`, `created_at` | Global admin reference data. Hard delete blocked while assets use it; deactivate instead |
| `Asset` | `site_id`, `facility_id`, `room_id` (nullable, `SET NULL`), `asset_type_id`, `asset_tag` (unique), `name`, `manufacturer`, `model_number`, `serial_number` (indexed), `install_date`, `purchase_date`, `purchase_cost` (Numeric 12,2), `warranty_expiration`, `expected_life_years`, `notes`, `condition_score`, `condition_label`, `is_active`, timestamps, `created_by_id` | `site_id` derived from Facility server-side. `condition_score`/`label` cache the newest history row via `Asset.apply_condition()` |
| `AssetConditionHistory` | `asset_id`, `assessed_at` (Date, indexed), `condition`, `score` (0–100, DB `CheckConstraint`), `inspector_id`, `reason`, `notes`, `recommended_action`, `created_at` | **Append-only**: SQLAlchemy `before_update`/`before_delete` listeners raise `ConditionHistoryImmutableError`, so any commit that mutates or deletes a row fails |
| `AssetAttachment` | `asset_id`, `condition_history_id` (nullable), `attach_file`, `uploaded_at`, `user_id` | Same pattern as Facility/Room attachments; `condition_history_id` links an assessment photo to its entry |

Condition scale lives in one place — `CONDITION_SCALE` / `condition_label_for_score()` —
and the label is always derived from the score server-side, never accepted from the form.

### Migration

`migrations/versions/f11aade72780_add_asset_and_asset_condition_tables.py` — four
`create_table`s with indexes, the unique constraints, the score check constraint, and FK
`ondelete` rules. Verified additive the same way as Phase 1: simulated a database with
every prior table, `db stamp 7d40fabd6b8b`, `db upgrade`, then `db migrate` → "No changes
in schema detected."

### Routes (`application/routes.py`, "Assets & Asset Condition (M&O)" section)

Asset types (admin): `asset_types`, `add_asset_type`, `edit_asset_type`,
`delete_asset_type`. Assets: `assets` (filters: search over tag/serial/name, site,
facility, type, condition label, active status; eager-loads facility/room/type to avoid
N+1 on the list), `add_asset`, `edit_asset` (detail), `delete_asset` (soft),
`add_asset_condition` (append), `asset_qr` (PNG), `download_asset_attachment`,
`delete_asset_attachment`. Helpers: `_asset_form_choices`, `_validate_asset_location`
(unique tag + room-belongs-to-facility), `asset_qr_url`. `_save_attachment` gained an
`extra` kwarg so the condition route can stamp `condition_history_id` on the photo row.

### Access model

Same as Phase 1 (GET site-scoped via `can_access_site`, mutations `is_admin()`), with one
deliberate widening: **recording a condition assessment is allowed for Admin, Specialist
and Technician** (`role_id` 1–3), site-scoped — technicians are the people inspecting
equipment. Asset type management, including the list, is admin-only like Sites/Titles.

### QR codes

`/asset_qr/<id>.png` renders on the fly with `qrcode[pil]` (new dependency), encoding the
asset's detail URL (`url_for(..., _external=True)`). Shown on the detail page with a
download link. No image is stored. Opening the encoded URL still requires login.

### Templates

`assets.html`, `add_asset.html`, `edit_asset.html`, `asset_types.html`,
`add_asset_type.html`, `edit_asset_type.html`, plus a shared
`includes/condition_badge.html`. Nav entries added (Assets under Facilities for everyone;
Asset Types under admin Settings), desktop and mobile.

### Phase 1 bug fixed along the way

`edit_facility.html` nested the add-floor `<form>` inside the main facility `<form>`.
That's invalid HTML: a browser drops the inner `<form>` tag and "Add Floor" would have
submitted the facility edit with the floor fields mixed in. The floors section now renders
outside the main form. The Phase 1 curl smoke test posted straight to `/add_floor` and so
never noticed — this phase's smoke test adds a nested-form depth check on rendered HTML
to catch that class of bug.

## What was tested

### Automated — `tests/test_assets.py` (24 tests), full suite 128 passing

- Asset types: list (admin 200 / regular 403), add, duplicate name rejected, edit,
  delete blocked while in use.
- Assets: regular user 403 on add and on edit POST, create (asserts `site_id` inherited,
  room linked, no condition yet), duplicate tag rejected, room-from-other-facility rejected,
  list search hit/miss, detail 200 for a same-site regular user.
- Condition history: two assessments append (newest first, correct labels, cache
  updated); a back-dated entry is stored but does **not** overwrite the current cache;
  score 150 rejected with no row written; assessment photo is linked to its entry; ORM
  update raises `ConditionHistoryImmutableError` and the value is unchanged after
  rollback; ORM delete raises and the count is unchanged; regular user 403.
- QR: PNG served with correct mimetype and magic bytes; encoded URL equals the
  `edit_asset` external URL and fetching its path returns the asset page; a Technician at
  another site gets 403 on both the QR and the page.
- Soft delete keeps the row and all four history entries.

### Manual — dev server driven with `curl`

Fresh seed → login → create asset type, facility, room, asset → asset detail → record a
condition (score 58) → detail shows "Fair (58)" and the reason → `/asset_qr/1.png` is
`image/png` with the PNG signature → `/assets?condition_filter=Fair` lists the asset →
facility and room detail pages 200. Server log clean. Rendered `edit_asset`,
`edit_facility`, `edit_room` HTML parsed for `<form>` nesting: max depth 1 on all three.

**Caveat (unchanged from Phase 1):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **"Asset and AssetType models, each belonging to a Facility"** — read as *Asset*
   belongs to a Facility (+ optional Room); `AssetType` is global reference data. A type
   scoped to one building (e.g. "Boiler" existing separately per facility) would make the
   type list unmanageable and defeat cross-facility reporting.
2. **AssetType CRUD was added** even though the prompt only lists Asset CRUD — assets can't
   be created without a type, so a minimal admin screen (list/add/edit/delete) was needed.
3. **Cached current condition on `Asset`** (`condition_score`/`condition_label`) is a
   denormalization not in the plan. Without it, the list page's condition filter/badge
   would need a correlated "latest history row" subquery per asset. The history remains
   the source of truth; the cache is only written by `apply_condition()`.
4. **Immutability is enforced in the ORM, not just by "no edit route".** The plan says
   "never overwritten"; a missing route doesn't guarantee that. Listeners make the
   guarantee hold for any code path through the session. (Raw SQL could still bypass it —
   a DB trigger would be the next step if that matters.)
5. **Back-dated assessments** are accepted (an inspector entering last month's inspection)
   but don't displace a newer entry as "current". The plan didn't address ordering.
6. **Condition recording is open to Technicians**, a widening of Phase 1's admin-only
   mutation rule — see Access model.
7. **One photo per assessment**, via the existing single-file mechanism; multi-photo is
   still deferred as in Phase 1.
8. **"QR URL resolution" is tested by asserting the encoded URL and fetching it**, not by
   decoding the PNG — decoding needs the `zbar` system library, which isn't worth adding
   for a test.
9. **Migration verified against a simulated baseline, not an empty DB** — same reason as
   Phase 1 (see `PHASE_0_ARCHITECTURE_ANALYSIS.md` §5).
10. **Not done, deliberately:** Work Orders, PM, Inspections, Asset Risk Score (Phase 8),
    QR codes for Facilities/Rooms (plan mentions them; this phase's prompt scopes QR to
    assets).

## Known follow-ups (not blockers)

- Browser/visual pass on the new templates.
- Room `<select>` on the asset form lists every active room labelled "Facility – Room"
  rather than filtering by the chosen facility client-side (same as the floor select in
  Phase 1). Server-side validation catches mismatches.
- `AssetType.category` is free text for now; Phase 3 introduces the admin-editable
  Category model and this should become an FK then.
- Nothing is committed yet — bootstrap + Phase 1 + Phase 2 are all in the working tree.
