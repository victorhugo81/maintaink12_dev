# Phase 0 — Repository & Architecture Analysis

*Written after the fact: Phase 1 (Facility/Floor/Room) was already implemented before this
document existed, based on the same exploration recorded here. Nothing in Phase 1 conflicts
with the recommendations below — see "Reconciliation with Phase 1" at the end.*

Scope note: this covers the AssistItK12 codebase as it existed in
`/Users/vsolis/Documents/Dev/assistitk12` at the time Maintaink12 was forked from it — the
foundation this repo (`maintaink12_dev`) started as a copy of.

## 1. Current models, their fields, and relationships

All models live in one file, `application/models.py` (174 lines pre-Phase-1). No base class,
no mixins — every model repeats its own `id`, timestamps, etc.

| Model | Key fields | Relationships |
|---|---|---|
| `Organization` | id (singleton, always `1`), `organization_name`, `site_version`, SMTP fields (`mail_*`), FTP fields (`ftp_host_enc`/`ftp_username_enc`/`ftp_password_enc` — Fernet-encrypted, plus schedule fields) | none (singleton config row) |
| `Notification` | `msg_name` (unique), `msg_content`, `msg_status` (`'active'`/`'inactive'` string) | none — a global banner message list, not a delivery mechanism |
| `Role` | `role_name` (unique) | `users` backref |
| `Site` | `site_name` (unique), `site_acronyms`, `site_cds`, `site_code`, `site_address`, `site_type` | `users` backref, `tickets` (`back_populates`) |
| `User` | `first_name`/`middle_name`/`last_name`, `email_enc`+`email_hash` (Fernet-encrypted email + HMAC lookup hash — see §6), `password` (werkzeug scrypt hash), `must_change_password`, `failed_login_attempts`, `locked_until`, `rm_num`, `role_id` FK, `site_id` FK, `status` (string `'Active'`/`'Inactive'`) | `role_id`→Role, `site_id`→Site, `created_tickets`/`assigned_tickets` backrefs from Ticket |
| `Ticket` | `title_id` FK, `tck_status` (string enum: `'1-pending'`/`'2-progress'`/`'3-completed'`, indexed), `created_at`/`updated_at`, `user_id` FK (creator), `site_id` FK, `assigned_to_id` FK (nullable), `escalated` (int 0/1) | `user`, `assigned_to` (both →User, disambiguated `foreign_keys`), `title`→Title, `contents`(1:N `Ticket_content`, cascade delete-orphan), `site` (`back_populates`), `attachments` (1:N `Ticket_attachment`, cascade delete-orphan) |
| `Title` | `title_name` (unique) — this is effectively "ticket category" | `tickets` backref |
| `Ticket_content` | `ticket_id` FK, `content` (text), `cnt_created_at`, `user_id` FK (nullable) | `ticket` (`back_populates`), `user`→User |
| `Ticket_attachment` | `ticket_id` FK, `attach_image` (stored filename), `uploaded_at`, `user_id` FK (nullable) | plain FK, backref via Ticket.attachments |
| `BulkUploadLog` | `filename`, `uploaded_at`, `uploaded_by_id` FK (nullable), `total_records`/`users_added`/`users_updated`, `status`, `error_message` | `uploader`→User |

Notable patterns:
- IDs are `db.Integer, primary_key=True, autoincrement=True` everywhere; no UUIDs.
- Timestamps use a module-level `_utcnow()` helper (naive UTC datetime — no tz-aware columns) as the `default=`/`onupdate=` callable.
- No soft-delete anywhere yet — every delete route (`delete_site`, `delete_role`, `delete_ticket`, `delete_title`, `delete_notification`) does a real `db.session.delete(...)`.
- `User.email` is a Python `@property`/`@email.setter` pair backed by two real columns (`email_enc`, `email_hash`) — encrypted at rest, looked up by HMAC hash since you can't query an encrypted column directly. Any new model that needs to look up a User by email must go through `hash_email()`, not `User.email ==`.
- Two-way relationships mix `backref` (older, one side declares both) and `back_populates` (explicit both sides) inconsistently — `Ticket.site`/`Site.tickets` uses `back_populates`, `Ticket.user`/`User.created_tickets` uses `backref`. No strong convention to follow here; `backref` is more common in the file.

## 2. Current routes/blueprints and how they're organized

Single Flask blueprint, `routes_blueprint`, registered once in `main.py`. Everything —
auth, admin CRUD, tickets — lives in one file, `application/routes.py` (2403 lines
pre-Phase-1), organized with `# ****** Section Header ******` comment banners rather than
sub-blueprints or a package structure:

- Two `@routes_blueprint.app_context_processor` functions near the top inject
  `active_notifications` (for the banner) and `app_version` (parsed from `CHANGELOG.md`)
  into every template.
- A `@routes_blueprint.before_request` (`enforce_password_change`) redirects any user with
  `must_change_password=True` to `/set-password` regardless of what they requested, with an
  explicit allow-list (`set_password`, `logout`, `static`) to avoid a redirect loop.
- Auth section: `login`, `logout`, `set_password`.
- Admin management sections, each following the same list/add/edit/delete shape: Users,
  Roles, Sites, Notifications, Organization/email config, bulk CSV upload (manual + FTP),
  Titles.
- Tickets section: `tickets` (list), `add_ticket`, `edit_ticket` (doubles as the detail page
  — comments + attachments live here), `add_comment` (AJAX/JSON), `delete_ticket`,
  `download_attachment`, `delete_attachment`.
- `index` — the dashboard (see §8).
- `profile` — self-service profile edit + change-password.

Route naming: `<verb>_<noun>` (`add_site`, `edit_site`, `delete_site`), plural `<noun>` for
the list route (`sites`), matching `url_for('routes.<name>')` and Jinja `request.endpoint`
checks in the nav for active-link highlighting. No REST-y `/sites/<id>` nesting — every
resource's routes are flat, `/edit_site/<int:site_id>` not `/sites/<int:id>/edit`.

Authorization is done with small guard functions called at the top of a view
(`is_admin()`, `is_tech_role()`, `can_access_ticket(ticket)` — see §6), not decorators or
Flask-Login's `@roles_required`-style extensions.

## 3. Current templates and shared UI components

`application/templates/`, flat (no subfolders except `includes/`). One template per
route-pair, mostly `<noun>.html` (list), `add_<noun>.html`, `edit_<noun>.html`. Notable
shared pieces:

- `base.html` — layout shell; includes `includes/nav.html` (sidebar + top navbar) and
  `includes/footer.html` (copyright, About modal, the shared `#deleteConfirmModal` and its
  `window.showDeleteConfirm(message, callback)` JS helper used by every delete button).
- List pages: a `.table-per-page-section` (per-page `<select>` that self-submits on
  `onchange`) + `flask_paginate.Pagination` rendered via `{{ pagination.links }}`, a
  `table-striped` Bootstrap table, and per-row action buttons (`table-button-edit`,
  `table-button-delete`) — the delete button doesn't submit directly, it opens
  `#deleteConfirmModal` via `data-bs-toggle`/`data-confirm-message`/`data-form-id` and JS
  submits the matching hidden `<form id="delete-<noun>-<id>">` on confirm.
- Filter bars: each filter is its own tiny `<form method="get">` that submits on
  `onchange`, carrying every *other* active filter forward as a hidden input so filters
  compose via repeated GETs rather than one shared filter form (see `tickets.html`).
- Add/Edit forms: `{{ form.hidden_tag() }}` (CSRF), a sticky "Save/Back" action bar pinned
  to the top of the form, and `<p class="text-uppercase ...">Section Name</p><hr>` dividers
  between field groups — no fieldset/legend, just visual sectioning.
- Detail-style pages (`edit_ticket.html`) go further: a two-column layout with a
  `position-sticky` left nav of in-page anchors (`<a href="#ticketstatus">`) and a right
  column of `<div class="ticket-content-box" id="...">` sections — one per concern (status,
  details, comments, attachments). Comments post via `fetch()` to a JSON endpoint
  (`add_comment`) and reload the page on success; attachment delete builds a throwaway
  `<form>` in JS and submits it after `showDeleteConfirm` confirms.
- Badges: `<span class="badge bg-{success|warning|info|...}">` for status, driven by simple
  Jinja `{% if %}` chains against the raw status string — no shared status→color helper.

Visual system: flat, bordered, no gradients (see `application/CLAUDE.md`'s "Frontend Visual
Design System" section) — CSS custom properties in `static/css/style.css`, no build step.

## 4. Current services/business-logic layer

There isn't one, in the sense of a `services/` package. Business logic lives inline in the
route functions in `routes.py`, with two small extractions:

- `application/utils.py` — pure helper functions with no Flask/DB coupling beyond
  `current_app.config`: `validate_password`, `validate_file_upload` (extension + magic-byte
  check + size cap), `encrypt_mail_password`/`decrypt_mail_password`/`hash_email` (Fernet +
  HMAC, keyed off `SECRET_KEY`), `get_app_version` (parses `CHANGELOG.md`).
- `application/email_utils.py` — `send_ticket_notification(event, ticket, **kwargs)` (one
  function, dispatches on an `event` string to build the right subject/body per ticket
  lifecycle event) plus `send_temp_password_email`/`send_password_updated_email`. All three
  check `_is_mail_configured()` first and swallow send failures into a logged error rather
  than raising — email delivery is best-effort, never blocks the request.
- `application/scheduled_jobs.py` — one function, `run_org_ftp_schedule()`, run by
  APScheduler on a cron trigger read from `Organization`'s FTP schedule fields. It's the one
  place with a real multi-step "business process" (download two CSVs over FTP, validate,
  upsert users, deactivate users missing from the feed) and it pushes its own app context
  explicitly since it doesn't run inside a request.

There's no repository/DAO layer either — routes call `Model.query....` directly.

## 5. Current migrations (Alembic/Flask-Migrate) and naming conventions

Standard `flask db migrate`/`db upgrade` via Flask-Migrate, files in
`migrations/versions/<hash>_<slug>.py`, slug auto-derived from the `-m` message
(`add_ftp_schedule_table`, `encrypt_user_email_field`, `drop_unique_constraint_on_site_cds_and_...`).
Every file follows the standard autogenerate skeleton (`upgrade()`/`downgrade()`, `#
### commands auto generated by Alembic - please adjust! ###` markers) — some are hand-edited
afterward (e.g. `encrypt_user_email_field` almost certainly needed a manual data-migration
step alongside the schema change, not just column add/drop). Column type changes on
sensitive tables use `op.batch_alter_table(...)` (SQLite-compatible batch mode, harmless on
MySQL) rather than bare `op.alter_column`.

**Important non-obvious fact, confirmed while setting up this repo's dev environment**: the
migration chain's root revision (`b47d77fd2576`, "add must_change_password to user") is
**not** runnable against a truly empty database — running `flask db upgrade` from empty
fails with `NoSuchTableError: bulk_upload_log`, because that migration's `upgrade()` assumes
the baseline tables (`user`, `ticket`, `site`, etc.) already exist. The real fresh-install
path is `installation/seed_data.py`, which calls `db.create_all()` directly and never touches
Alembic — the migration chain exists purely to carry an *already-running* production database
forward. Any new migration must be additive against that assumption, and testing "does this
migration apply cleanly to production" means simulating a database that already has every
table except the one(s) being added — not starting from empty. See
`application/CLAUDE.md`'s Database section for the exact recipe used to verify the Phase 1
migration (`7d40fabd6b8b_add_facility_floor_room_tables`) this way.

## 6. Current auth/authorization mechanism and how roles/permissions are checked

- Flask-Login (`login_user`/`logout_user`/`current_user`/`@login_required`) for session
  auth; `login_manager.user_loader` loads `User` by primary key.
- Roles are a flat FK to `Role`, but authorization checks are almost entirely by **numeric
  `role_id`** hardcoded in `routes.py`, not by `role_name` string or a permissions table:
  `1`=Admin, `2`=Specialist, `3`=Technician, everything else (`4`+, e.g. Teacher/Staff) is
  "regular user." `User.is_admin`/`User.is_tech_role` are the only named `@property`
  wrappers; most checks just compare `current_user.role_id` inline.
- Three reusable guard functions (called imperatively at the top of a view, not decorators):
  `is_admin()` (abort 403 unless `role_id == 1`), `is_tech_role()` (abort 403 unless
  `role_id in (2, 3)`), and `can_access_ticket(ticket)` (returns bool: Admin/Specialist see
  any ticket, Technician is scoped to their own site matching the `/tickets` list filter,
  everyone else only tickets they created or are assigned). `can_access_ticket` is the one
  place object-level (not just role-level) authorization is centralized — every
  ticket-detail-style route (`edit_ticket`, `add_comment`, `download_attachment`,
  `delete_attachment`) must call it, per an explicit warning in `application/CLAUDE.md` (a
  past bug let Technicians reach other sites' tickets by guessing a ticket ID before this
  existed).
- `edit_user` has extra re-validation: a tech-role (non-Admin) editor's submitted
  `role_id`/`site_id` choices are checked server-side again inside `validate_on_submit()`,
  not just constrained in the rendered `<select>` — the original bug this fixed let a
  tech-role account escalate itself to Admin.
- Login itself is hardened against enumeration: a dummy password hash
  (`_DUMMY_PASSWORD_HASH`) is compared even when no such user exists, so timing doesn't leak
  which accounts exist; one generic failure message covers "no such user," "wrong password,"
  "inactive," and "locked" alike. Failed attempts increment a per-user counter with a
  15-minute lockout after 5 attempts, tracked in `User.failed_login_attempts`/`locked_until`.
- CSRF: global `CSRFProtect` (`csrf.init_app(app)`) protects every POST by default;
  `flask_wtf.FlaskForm`'s own `hidden_tag()` also emits a token, so forms are double-covered.

## 7. Current notification system

Two entirely separate things share the word "notification" in this codebase — worth
flagging so a later phase doesn't conflate them:

1. **`Notification` model** (admin-managed): a short list of global banner messages
   (`msg_name`, `msg_content`, `msg_status`), CRUD'd like any other admin resource, injected
   into *every* template via the `inject_active_notifications` context processor and
   presumably rendered as a dismissible banner in `base.html`. This is a static
   announcements feature, not a delivery mechanism — think "district-wide heads-up banner,"
   not "you have a new message."
2. **Ticket email notifications** (`application/email_utils.py`): synchronous
   `Flask-Mail` sends, triggered directly from the route that caused the event (ticket
   created/status-changed/assigned/escalated/commented), addressed to the relevant
   user(s) by their (decrypted) email. Gated on `_is_mail_configured()` (Organization has
   SMTP creds saved) and wrapped in try/except so a mail failure never breaks the HTTP
   request — it just logs an error. There is no queue, no retry, no per-user notification
   preferences, and no in-app notification inbox; email is the only delivery channel.

Neither system has a per-record "who should be told" abstraction beyond what's hardcoded in
`send_ticket_notification`'s per-event branches — extending notifications to Facility/Room/
future WorkOrder events means adding new branches (or a small refactor) here, not plugging
into an existing generic notifier.

## 8. Current dashboard implementation and how it queries data

`index()` (the `/` route) is the only dashboard, computed entirely inline at request time —
no caching, no background aggregation job, no pre-computed rollup table:

- Role-gated site scope: Admin/Specialist (`role_id` 1/2) get a site `<select>` and can
  filter by any site; Technician (`3`) and everyone else are locked to their own
  `current_user.site_id`.
- An optional `?year=` filter, with the available-years list itself derived from a
  `DISTINCT EXTRACT(YEAR FROM created_at)` query over `Ticket` — not a hardcoded range.
- Three status counts (pending/in-progress/completed) via three separate `.filter(...).count()`
  calls sharing a `query_filter` list built up conditionally — the same three-query shape is
  duplicated verbatim for each of the three role branches (Admin/Specialist, Technician,
  regular user) rather than being factored into one shared helper parameterized by scope.
- "Top 5 titles" via one `GROUP BY`/`ORDER BY count DESC LIMIT 5` query (efficient).
- Month-of-year and day-of-week histograms are **not** done in SQL — the route pulls
  `db.session.query(Ticket).filter(*query_filter).all()` (every matching row, into Python)
  **twice** (once for the month loop, once for the weekday loop) and buckets in a Python
  dict. This is the one clear N+1-adjacent / scaling concern already present pre-Phase-1: it
  loads every ticket matching the filter into memory, twice, on every dashboard view. Given
  PROJECT_PLAN.md's performance baseline (hundreds of thousands of work orders), the
  Maintaink12 dashboards phase (Phase 9) should do these bucketed counts in SQL
  (`GROUP BY EXTRACT(MONTH ...)` / `GROUP BY EXTRACT(DOW ...)`) rather than copying this
  pattern forward, and should not assume "small enough to just loop in Python" holds at
  M&O's target scale.
- Chart rendering is Chart.js, fed the pre-aggregated Python lists (`months`/`counts`,
  `weekdays`/`weekday_counts_list`) as template context — no client-side aggregation.

## 9. Current site-scoping model

- Every `User` has exactly one `site_id` (required, not nullable) — no multi-site users.
- `Ticket.site_id` is set directly from `current_user.site_id` at creation
  (`add_ticket`) — a ticket's site is "whoever filed it's site," not independently chosen.
- Scoping is enforced per-route, not via a global before-request filter or a SQLAlchemy
  query-level default filter: each list/detail route re-derives the same three-tier rule
  (Admin/Specialist = all sites, Technician = own site only, everyone else = own
  records only) inline. `can_access_ticket()` centralizes this for ticket *detail* access,
  but the *list* route (`tickets()`) has its own separately-written copy of the same logic
  applied to the query — there is no single shared "scope this query to what
  `current_user` can see" helper reused across both.
- Sites themselves (`Site` CRUD) are Admin-only end-to-end — even *viewing* the sites list
  requires `role_id == 1`; there's no "any user can see basic site info" tier for that
  particular resource.

This is exactly the rule PROJECT_PLAN.md asks Maintaink12 to preserve ("district admins see
everything, site-level users see only their authorized sites") — it already exists, just
reimplemented per-route rather than as a shared utility.

## 10. Existing tests, and what they cover

Pytest, `tests/conftest.py` provides a session-scoped `app` fixture (SQLite in-memory,
CSRF/rate-limiting disabled, `db.create_all()` + a hand-seeded baseline: 4 roles, 1 site, 1
Organization row, one Admin user, one "Teacher"-role regular user) and three client
fixtures (`client` unauthenticated, `admin_client`/`user_client` pre-authenticated via
directly stuffing `session['_user_id']` rather than posting `/login`). Five test files,
~84 tests pre-Phase-1:

- `test_auth.py` — login success/failure, lockout behavior, password-change enforcement.
- `test_crud.py` — Roles/Sites/Titles/Notifications CRUD, each with an explicit
  "regular user gets 403" case.
- `test_tickets.py` — create/view/comment/admin-delete, plus a `test_ticket_edit_page_loads`
  that accepts either `200` or `403` since it doesn't control which ticket happens to exist.
- `test_security.py` — presumably CSRF/XSS/IDOR-style checks (not read in detail here; not
  needed for the Facility/Room scope).
- `test_users.py` — user CRUD + the escalation-prevention edit_user re-validation.

Convention worth following: tests are session-scoped against one shared DB, so later test
classes rely on records created by earlier ones (e.g. `test_edit_site` looks up "Test
School" by name, `pytest.skip()`-ing if a prior test didn't create it) rather than each
test setting up its own isolated fixtures. New test modules should follow this same
"session-shared DB, `filter_by(name=...).first()` then skip-if-missing" pattern rather than
introducing per-test transactions/rollback, to stay consistent — and take care that any POST
your test sends includes every field a real form submission would (see the `is_active`
checkbox gotcha in `application/CLAUDE.md`'s Key Conventions).

## Recommendation 1 — Where Facility → Floor → Room → Asset fits

Add them as new top-level models in the same flat `application/models.py`, not a
`Facility(Site)` subtype or STI. `Site` stays the top of the hierarchy exactly as-is;
`Facility.site_id` is a plain FK to it, same shape as `Ticket.site_id`/`User.site_id`.
`Floor` FKs to `Facility`; `Room` FKs to `Facility` (and optionally `Floor`); `Asset` (a
later phase) will FK to `Facility` and optionally `Room`. No new abstract base class is
needed to introduce this — the codebase has never used one, and every model already repeats
its own `id`/timestamp columns, so a lone new `TimestampedModel` mixin for just the M&O
tables would be an inconsistency, not a cleanup.

The one deliberate deviation from existing convention: **soft delete**
(`is_active`) on `Facility`/`Room`, where every existing model hard-deletes. This isn't
optional — PROJECT_PLAN.md requires that later phases (assets, work orders, inspections,
cost history) keep referencing a facility/room after it's "removed" from active use, and
none of those can tolerate their parent row vanishing out from under them. `Floor` doesn't
need this: it has no history hanging off it directly, so it hard-deletes, blocked only while
it still has `Room`s pointing at it (an application-level check, not a DB constraint, since
SQLite in tests doesn't enforce `ON DELETE` behavior the way MySQL does in production).

## Recommendation 2 — WorkOrder: new model vs. extending Ticket

**New model, not an extension of `Ticket`.** Reasons, in order of weight:

1. **Optional requester is structural, not cosmetic.** Every `Ticket` column that matters
   for identity assumes a human filed it: `user_id` is `nullable=False` (the creator), and
   `add_ticket()` sets `site_id`/`user_id` unconditionally from `current_user`. Making a
   work order "PM-generated" or "Inspection-triggered" with no requester means either making
   `Ticket.user_id` nullable (a destructive-adjacent schema change to a column every
   existing query/template assumes is populated — `ticket.user.get_full_name()` appears
   unguarded in `tickets.html`) or adding a fake system user, which is a workaround for the
   wrong data model, not a fix.
2. **Status/priority vocabularies genuinely don't overlap.** `Ticket.tck_status` is a
   3-value string enum (`1-pending`/`2-progress`/`3-completed`) hardcoded into the sort
   logic in `tickets()` (`case()` expression keyed on those exact literal strings) and into
   `email_utils.STATUS_LABELS`. PROJECT_PLAN.md's work order status list has 11 values
   (New, Assigned, Scheduled, In Progress, Waiting for Parts/Vendor/Approval, On Hold,
   Completed, Cancelled, Closed) with real workflow transitions to enforce — squeezing that
   into `tck_status` means either overloading one column with two incompatible vocabularies
   (breaking every place that pattern-matches on the ticket ones) or adding a parallel
   `wo_status` column on the same table, at which point it's no longer really "extending
   Ticket," it's two independent entities sharing a table for no benefit.
3. **The relationships genuinely differ.** A WorkOrder needs optional FKs to `Facility`/
   `Room`/`Asset` (added over Phases 1–2) plus, later, `WorkOrderLabor`/`WorkOrderMaterial`/
   `MaintenancePlan`/`Inspection`/`Vendor` links that have no IT-ticket equivalent.
   `Ticket_content`/`Ticket_attachment` already exist as separate child tables (not columns
   on `Ticket`) specifically so they can be reused as a *pattern* — Phase 3's
   `WorkOrderComment`/`WorkOrderAttachment` should copy that same child-table shape rather
   than trying to reuse `Ticket_content`/`Ticket_attachment` directly, since those already
   FK to `ticket_id` specifically.
4. **What genuinely should be reused is the *pattern*, not the *table*.** Requester
   submission UX (the simple "what/where/type/urgency/description/photo/submit" flow),
   `validate_file_upload`-based attachment handling, the assignment/notification hookup
   (`assigned_to_id` + `send_ticket_notification`-style event dispatch), and the
   `can_access_ticket`-style centralized per-record authorization helper are all worth
   copying *as conventions* for WorkOrder — which is exactly what Phase 1 already did for
   Facility/Room (`can_access_site()` mirrors `can_access_ticket()`'s shape; attachment
   upload mirrors `validate_file_upload` + a per-entity folder). Phase 3 should do the same:
   a new `WorkOrder` model, built with Ticket's *conventions*, not Ticket's *table*.

## Recommendation 3 — Conventions the new M&O modules should follow

Already applied in Phase 1, and the ones Phase 3+ should keep following:

- **One flat model file, one flat routes file, one flat template-per-route-pair** — no
  blueprints-per-module split. The codebase has never used sub-blueprints; introducing one
  just for M&O would make it the only inconsistent corner of the app.
- **`<verb>_<noun>` route naming**, flat URLs (no REST nesting), matching `url_for` calls and
  nav active-state checks against `request.endpoint`.
- **Imperative guard functions** (`is_admin()`, and now `can_access_site()`) called at the
  top of a view, not decorators — stay consistent with `is_admin()`/`is_tech_role()`.
- **List page shape**: per-page selector + `flask_paginate.Pagination`, one small
  self-submitting `<form method="get">` per filter carrying every other filter forward as a
  hidden input, `table-button-edit`/`table-button-delete` action buttons, delete routed
  through the shared `#deleteConfirmModal` + `showDeleteConfirm()` JS helper.
- **Detail/edit page shape** for anything with sub-resources (comments, attachments,
  floors): the `edit_ticket.html` two-column sticky-nav-plus-`ticket-content-box`-sections
  layout, reused verbatim in `edit_facility.html`/`edit_room.html`.
- **Migrations**: additive only, autogenerated via `flask db migrate`, verified against a
  simulated "production already has every other table" baseline rather than an empty DB
  (see §5) — never assume a clean-slate migration run is a valid test.
- **Attachments**: a dedicated `<Entity>Attachment` child table + a per-entity upload
  folder (`UPLOAD_<ENTITY>_ATTACHMENT` in `main.py`) + `validate_file_upload`, matching
  `Ticket_attachment`'s shape — not one polymorphic attachments table.
- **Soft delete only where later phases need the history** (Facility, Room, and — per
  Recommendation 2 — WorkOrder later); hard-delete for pure reference/config data (Floor,
  and existing Role/Title/Notification-style tables), matching what already exists.

## Reconciliation with Phase 1

Phase 1 (Facility/Floor/Room, already implemented in this repo) matches every convention
above: flat model/routes/template files, `can_access_site()` mirroring
`can_access_ticket()`, `<Entity>Attachment` child tables with per-entity upload folders,
soft delete on Facility/Room only (Floor hard-deletes, blocked while it has rooms), and a
migration verified additive against a simulated pre-existing-production baseline rather than
an empty database. No changes to Phase 1's code are implied by this analysis — this document
exists to make explicit, and get sign-off on, decisions that were made inline while building
it, particularly Recommendation 2 (WorkOrder vs Ticket), which Phase 1 didn't need to
resolve but Phase 3 will.
