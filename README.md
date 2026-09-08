# Maintaink12

A lightweight CMMS / work-order management system for K-12 district Maintenance & Operations
(M&O) departments — built on the [AssistItK12](https://github.com/victorhugo81/assistitk12)
codebase (Flask + Bootstrap + MySQL). Maintaink12 is a separate project from AssistItK12
(no shared git history); it started as a copy of that codebase and diverges from there.

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

Seed roles, a default site, an admin user, and ticket titles:

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
