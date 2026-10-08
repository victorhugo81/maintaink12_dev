# Maintaink12

A CMMS for K-12 district Maintenance & Operations (M&O) departments — built on the
[AssistItK12](https://github.com/victorhugo81/assistitk12) codebase (Flask + Bootstrap +
MySQL). Maintaink12 is a separate project from AssistItK12 (no shared git history); it
started as a copy of that codebase and diverges from there.

Covers the full M&O workflow: Facilities/Rooms/Assets with condition tracking and QR
codes; Work Orders with a status-transition workflow, labor/material cost tracking, and
Vendor assignment; Preventive Maintenance plans that auto-generate work orders on a
schedule; a Facilities Audit workflow (checklist-based room-by-room walkthroughs, grouped
into yearly Inspection Cycles, with completion-% rollups); Capital Projects and
Asset-Risk/Capital-Replacement scoring; SLA targets with breach/warning tracking and an
automated notification sweep; a role-based M&O Dashboard and a CSV-exportable Reports
section; global search and a generalized CSV import tool; and an automatic, field-level
audit log.

See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the full vision, data model, and
canonical KPI list, and [`docs/MAINTAINK12_PLAN_AND_PROMPTS.md`](docs/MAINTAINK12_PLAN_AND_PROMPTS.md)
for the phased build-out this repo follows. [`application/CLAUDE.md`](application/CLAUDE.md)
documents the architecture and conventions in more detail.

## Running the app

Dependencies are managed with [uv](https://github.com/astral-sh/uv):

```bash
uv sync
```

Create a `.env` (not committed) with at least:

```
FLASK_CONFIG=development
SECRET_KEY=some-local-dev-secret
DATABASE_URL=sqlite:////absolute/path/to/instance/dev.db
```

(MySQL via `mysql+pymysql://...` is what production uses — see `config.py`.)

Seed roles, a default site, and an admin user:

```bash
uv run python installation/seed_data.py
```

Then run the dev server:

```bash
uv run flask --app main.py run
```

## Tests

```bash
uv run pytest
```
