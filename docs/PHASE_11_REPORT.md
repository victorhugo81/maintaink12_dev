# Phase 11 Report — SLA Rules, Notifications, Global Search, CSV Import

Status: **complete**. Definition of done met — configurable SLA rules with breach/warning
detection and dashboard surfacing, an 8-category notification sweep with per-user
preferences, cross-entity global search, and a generalized CSV import tool for five entity
types, all with pre-commit validation and rollback guarantees; 67 new tests, 435/435 total,
no regressions.

Companion docs: `PROJECT_PLAN.md`, `PHASE_0_ARCHITECTURE_ANALYSIS.md`, `PHASE_1_REPORT.md`
through `PHASE_10_REPORT.md`, `../application/CLAUDE.md`.

## What was built

Four largely independent features, sharing one migration (four new tables, no changes to
any existing table — no dialect workaround needed this phase).

### 1. SLA Rules (`SLARule`, `application/sla.py`)

One admin-configured rule per Priority (unique `priority_id`) with a response-hours and a
resolution-hours target. A priority with no row has no SLA tracking at all — never a
fabricated deadline. Both deadlines are computed on read (`created_at + rule.hours`), never
stored on the work order, so changing a rule applies to a work order's remaining lifetime
rather than freezing a stale snapshot. Each metric has four states — `met`/`breached`/
`warning` (last 20% of the window)/`pending` — evaluated independently, so a work order can
warn on response while still pending on resolution.

**Response tracking reuses Phase 9's existing `started_at`-based proxy** for "responded to"
(time to IN_PROGRESS) rather than adding a new `first_response_at` column. A real
first-response timestamp would be more accurate (response more naturally means "assigned/
acknowledged," not "work started"), but every existing test builds WorkOrder fixtures
directly via the ORM, never through `workflow.apply_transition()` — a new column stamped
only by that function would read `None` for all of them, and Phase 9's own `avg_response_days`
KPI already made this exact tradeoff for the same reason. Reusing it keeps one definition of
"response" project-wide instead of two competing ones.

`analytics.work_order_kpis()`'s SLA Compliance % is now dual-mode: it tries the real
SLARule-based calculation first (one cheap COUNT query if no active rule exists anywhere —
the common case before a district configures anything) and falls back to the original
due-date approximation when no configured rule covers any of the period's completions. This
is backward compatible by construction: every Phase 9/10 test that never creates an SLARule
takes the same code path it always did.

The M&O Dashboard (Executive/Manager) gained an "SLA Breaches & Warnings" panel
(`analytics.sla_summary()`) listing every open-or-recently-completed work order past or
approaching its deadline, plus KPI cards and an Operational Insight
("N work order SLAs are breached..."). Admin CRUD at `/sla_rules` (list) and
`/edit_sla_rule/<priority_id>` (get-or-create, since there are only a handful of priorities
and no separate "add" flow is warranted).

### 2. Notifications (`application/notifications.py`, `NotificationPreference`, `NotificationLog`)

The eight categories named in the phase: PM due/overdue, inspection due/failed, vendor
contract expiration, asset warranty expiration, SLA warning/breach. Two trigger shapes:

- **Scan-based** (everything except inspection_failed): `run_notification_sweep()` runs
  daily via APScheduler (2am, one hour after PM generation so a schedule the PM job just
  advanced past "today" doesn't also fire a same-day "PM due" alert), finds the current
  condition set per category, and notifies every eligible recipient once per condition.
- **Event-based** (inspection_failed): fired once, immediately, from
  `routes.record_inspection_results()` right after a failed result is recorded — never part
  of the sweep.

**"Without over-notifying"** is handled by `NotificationLog`'s dedup ledger — one row per
(event_type, entity_type, entity_id, bucket, user_id), unique-constrained. A transient
condition (due today) buckets by day (reminds once); a persistent one (overdue, expiring,
breached) buckets by ISO week (reminds on a cadence rather than every single run).
`NotificationPreference` is one row per user with a boolean per category; a user with no row
is treated as "everything on," so a new account doesn't silently miss alerts before ever
visiting `/notification_preferences`. Recipients are every active Admin/Specialist for every
category, plus the specific assigned technician for SLA warning/breach on their own work
order.

### 3. Global Search (`application/search.py`, `/search`)

One ILIKE query per entity type — work orders (number/title/description), facilities
(name/building code), rooms (number/name), assets (tag/serial/name), vendors (name/contact),
projects (name), users (name only) — each capped at 10 results, site-scoped the same way
every other cross-entity view in this project is. Users are searchable by name only: email
is encrypted at rest (`User.email_enc`/`email_hash`), so a partial match against it isn't
possible, only an exact hash match is — and the Users group is Admin-only, matching every
other user-management view.

### 4. CSV Import (`application/csv_import.py`, `CsvImportLog`, `/csv_import`)

A separate, self-contained tool from the legacy `/bulk-data-upload` (Users+Sites,
FTP-schedulable — untouched) covering Facilities, Rooms, Assets, Vendors, and Users. One
uniform two-phase shape:

1. `validate_rows()` — pure, no DB writes. A row missing a required field, referencing
   something that doesn't exist (unknown site/facility/role/asset type), or malformed
   (bad date/number) is marked `error`. A row whose natural key already exists in the
   database, or repeats an earlier row in the same file, is marked `duplicate`. Neither is
   fatal — both are excluded from the commit and reported per-row.
2. `commit_rows()` — every row that passed validation is added to ONE session and committed
   together. An exception during that commit rolls back the WHOLE transaction, so a failure
   can never leave the database partially written from that batch — and, since each upload
   is its own transaction, a failed batch can't retroactively corrupt an *earlier*,
   separately-committed successful import either.

A downloadable per-type CSV template (required + optional columns, one filled example row)
and a row-by-row report page (status badge + message per row, plus success/duplicate/error
counts) round out the tool. Every import is logged to `CsvImportLog` for an audit trail.

## Bugs found and fixed

- **`_notify_sla()` passed an already-resolved dict where a raw list was expected.** It
  called `sla.rules_by_priority()` (returns `{priority_id: rule}`) and handed that dict
  straight to `sla.scan(candidates, rules, now)`, whose `rules` parameter expects raw
  SLARule objects — it calls `rules_by_priority()` internally itself. Iterating a dict
  yields its keys (priority-id integers), so `rules_by_priority()`'s internal
  `{r.priority_id: r for r in rules}` blew up with `AttributeError: 'int' object has no
  attribute 'priority_id'` on the first real-data smoke test (unit tests with mocked
  `_Wo` objects didn't exercise this path). Fixed by checking rule existence directly and
  passing `rules=None`, letting `scan()` resolve rules itself.
- **A reordered test run surfaced real cross-file fragility, not a test bug to shrug off.**
  `pytest tests/test_search_and_csv_import.py tests/test_sla_and_notifications.py
  tests/test_dashboard.py ...` failed a Phase 9 test (`test_period_metrics`) that had been
  green in every prior phase. Root cause: `analytics.work_order_kpis()`'s new dual-mode SLA
  calculation checks whether *any* active SLARule exists *anywhere* — not scoped to the
  facility or filter being queried — and several of this phase's own tests leave a
  persistent, active SLARule behind for Priority 'High', which is the default priority in
  nearly every other test file's `_ids()`/`_wo()` helper. Once that rule existed, Phase 9's
  otherwise-unrelated 'High'-priority completed work orders got swept into the new
  rule-based calculation instead of the due-date approximation the test expected. This is
  correct, intended PRODUCTION behavior (configuring an SLA rule for a priority should
  affect every work order of that priority) but a real test-isolation hazard given this
  project's shared, session-scoped test database. Fixed by moving every test that leaves an
  active SLARule behind to Priority 'Critical' — unclaimed by any other file's defaults —
  and documented as a hard rule in `application/CLAUDE.md`: any test introducing
  admin-configurable *global* state must avoid the commonly-defaulted values other files
  rely on, and should be checked with at least one reordered run.

## What was tested

### Automated — 67 new tests across two files, full suite 435 passing

**`tests/test_sla_and_notifications.py`** (30 tests): the SLA state machine (response
met/breached via started_at, breached-with-no-response-yet, warning inside the last 20% of
the window, pending well before it; resolution met/breached via completed_at; the due-at
helpers against exact hand-computed datetimes); `scan()` finding breach and warning
separately, skipping Cancelled work orders and unruled priorities, and one work order
breaching both metrics independently; `aggregate_compliance()`'s met/total counts and its
`(0, 0)` no-rules signal; the analytics.py dual-mode integration (falls back to due-date
with no rule, uses the real rule and gets the correct 0%/100% when one is configured,
`sla_summary()` unavailable with zero active rules — reset mid-test to be order-independent
— and reporting a real breach when a rule exists); SLA rule routes (403 for non-admin,
create/edit, delete); notification preference resolution (no row = everything allowed, a
disabled preference is honored); the sweep's PM-overdue dedup (notifies once, second run
sends zero, log-row count unchanged); dedup is per-bucket not forever (a week-bucketed vendor
alert notifies again once the ISO week changes); a preference disabled for every recipient
blocks the alert entirely; `inspection_failed` fires exactly once regardless of how many
times it's called; the sweep never sends `inspection_failed` itself; an SLA breach notifies
both staff and the assigned technician and dedups on a second sweep; the
`record_inspection_results` route actually triggers the notification end-to-end.

**`tests/test_search_and_csv_import.py`** (37 tests): search below the minimum length
returns nothing; finds a work order by number and by title fragment; finds an asset by tag
and by serial number; finds facilities/rooms/vendors/projects; site scoping excludes another
site's facility entirely; users are excluded unless `include_users=True`; the route hides the
Users group for a non-admin and shows it for an admin (checked via the rendered result row,
not the bare query string the search box also echoes back, and not the literal word "Users"
which also appears in the nav); every CSV validation rule (valid row, missing required
field, unknown site/facility/role/asset-type reference, bad date/number, DB duplicate,
in-file duplicate, mixed valid+invalid rows in one file, bad vendor email, duplicate user
email); commit writes valid rows, an empty valid list is a no-op, a simulated commit failure
rolls back the *entire* batch (including rows before the failure point), and — the specific
guarantee named in the phase — a failed batch does not corrupt an earlier, already-committed
successful import; CSV import routes (admin-only, 404 on an unknown entity type, template
download, the upload report's success/duplicate/error counts, the route actually creates the
record, the audit log is written, a non-CSV file is rejected).

### Reordering checks

Ran `tests/test_search_and_csv_import.py tests/test_sla_and_notifications.py
tests/test_dashboard.py tests/test_reports.py tests/test_work_orders.py
tests/test_inspections.py tests/test_pm.py tests/test_vendors.py` and the reverse order —
this is the run that caught the priority-collision bug above; both directions pass after the
fix. Also ran `tests/test_sla_and_notifications.py tests/test_reports.py tests/test_dashboard.py
tests/test_labor_and_costs.py tests/test_projects_and_risk.py` as a second combination.

### Manual — dev server driven with `curl`

Seeded a facility/asset/vendor/work-order/PM-schedule with real breach/expiring conditions
(a 10-hour-old New work order against a 4-hour response target; a vendor contract and an
asset warranty each expiring within the week; a PM schedule 2 days overdue) and an SLARule
for the seeded priority. Confirmed: the M&O Dashboard's SLA panel shows both "SLA Breaches"
and "SLA Warnings" cards with the seeded work order listed; `run_notification_sweep()` fired
exactly the expected categories (`pm_overdue: 1, vendor_contract_expiring: 1,
asset_warranty_expiring: 1, sla_breach: 2` — both response and resolution breached for that
work order); global search found the asset by tag and the work order by number; the CSV
import template downloaded correctly and a 3-row upload (1 valid, 1 in-file duplicate, 1
unknown-site error) produced exactly that report and created exactly one new facility,
confirmed present on the facilities list afterward. Every request required a real CSRF token
(dev config has CSRF enabled, unlike the test suite) — confirmed the upload form's token
round-trips correctly. Server log clean throughout.

**Caveat (unchanged):** no visual/JS pass in a real browser.

## Deviations from the plan (and why)

1. **Response SLA reuses Phase 9's `started_at`-based proxy rather than a new
   first-response timestamp** — see the SLA section above; documented as the single biggest
   interpretation call this phase, made to avoid a second, competing "responded to"
   definition and to keep every existing fixture-building pattern in this project working
   unchanged.
2. **SLA breach/warning detection is a bounded Python scan, not pure SQL** — same tradeoff
   already accepted for Recurring Issue Detection (Phase 9) and Facility Health (Phase 9):
   evaluating a per-row state machine against admin-configured rules doesn't reduce cleanly
   to a `GROUP BY`. Capped at `sla.SLA_SCAN_LIMIT` (5,000), candidates limited to open work
   orders plus ones completed in the last 14 days.
3. **CSV Import is a new, separate tool, not an extension of the legacy `/bulk-data-upload`
   flow.** That flow's Users/Sites import is all-or-nothing per file (raises and aborts on
   the first invalid row, no per-row report) and is wired to FTP scheduling unrelated to
   this phase's ask. Building a second, uniform tool for the five *named* entity types was
   lower-risk than reshaping code an existing, working feature depends on, and the two
   coexist without conflict (different routes, different models for their audit trail).
4. **"Vendor contract expiration" covers `contract_end_date` only**, not
   `insurance_expiration`/`license_expiration` (which Vendor also carries and Phase 6's
   Vendor Performance page already surfaces) — the phase names "vendor contract expiration"
   specifically; the other two dates are a natural, listed follow-up.
5. **Global search has no dedicated search box in the top navbar** — added as a "Search"
   nav link + its own page instead, to avoid restructuring the shared `base.html`/`nav.html`
   top-bar markup (used on every page) for a single new feature. Functionally equivalent,
   one extra click.

## Known follow-ups (not blockers)

- Vendor insurance/license expiration notifications (contract-only today, per the deviation
  above).
- A real first-response timestamp (`WorkOrder.first_response_at`, stamped the moment status
  first leaves New) would make both the SLA response metric and Phase 9's `avg_response_days`
  more accurate than the `started_at` proxy — a schema change touching every existing test
  fixture's assumptions, better done as its own deliberate phase than folded in here.
- CSV Import's per-row validation queries the DB once per row (facility/site/role lookups) —
  fine at typical single-file import volume; a large bulk import could batch-prefetch
  reference data the way `_process_sites_rows`'s existing site-cache pattern already does.
- Browser/visual pass on all the new pages (SLA Rules, Notification Preferences, Search,
  CSV Import).
- Nothing is committed yet — bootstrap + Phases 1–11 are all in the working tree.
