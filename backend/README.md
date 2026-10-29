# Backend workspace (outside main/)

Application source lives in ../main/backend/app. This folder holds everything the
AGENTS.md rule keeps out of main/: dependencies, lock file, configuration,
migrations, the SQLite database, runtime image data and tests.

## Setup

    cd backend
    uv sync

## Migrate + run a single worker on loopback

    uv run alembic upgrade head
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --app-dir ../main/backend

## Checks

    uv run ruff check .
    uv run pytest -q

## Layout

- pyproject.toml / uv.lock: dependencies and lock
- .env.example: empty configuration sample (no credentials)
- alembic.ini / migrations: schema migrations
- data/: runtime data dir (SQLite db + references/generated/tmp), created on boot
- tests/: full HTTP test suite against a migrated temporary SQLite + image dir
