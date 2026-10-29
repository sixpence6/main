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

## Deploying on another machine

1. Install dependencies with `uv sync`.
2. Copy `.env.example` to `.env` and fill in the provider values. The file is
   git-ignored and is the only per-machine configuration.
3. Run `uv run alembic upgrade head`.
4. Start the server with the command above.

The production registry already contains the `openai-images` visual adapter.
Point `VISUAL_BASE_URL` at an OpenAI-compatible API root ending in `/v1`, set
`VISUAL_MODEL`, and choose a supported `VISUAL_IMAGE_SIZE` (currently
`1024x1024`). No code edits or per-machine adapter registration are required.
The adapter supports text-to-image and one reference image, and requests
base64 image data so result download never depends on a signed CDN URL.

`LLM_ADAPTER` remains unset until a language-model adapter is registered; the
rest of the app (history, configuration diagnostics, and visual generation)
still boots and reports that state explicitly.

## Checks

    uv run ruff check .
    uv run pytest -q

## Layout

- pyproject.toml / uv.lock: dependencies and lock
- .env.example: empty configuration sample (no credentials)
- alembic.ini / migrations: schema migrations
- data/: runtime data dir (SQLite db + references/generated/tmp), created on boot
- tests/: full HTTP test suite against a migrated temporary SQLite + image dir
