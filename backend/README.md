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

The production registry already contains the `openai-chat` language-model
adapter and the `openai-images` visual adapter. Point both base URLs at
OpenAI-compatible API roots ending in `/v1`, set the model names, and choose a
supported `VISUAL_IMAGE_SIZE` (currently `1024x1024`). No code edits or
per-machine adapter registration are required.

The chat adapter handles requirement organization through `POST /chat/completions`
with JSON-object responses; it returns either a clarifying
question (`needs_input`) or a final review with a non-empty prompt. Set the
optional `LLM_VISION_MODEL` to send the project reference image as a multimodal
content part, so the organizer can read the product image; when it is blank the
organizer stays text-only. If the multimodal model is served under a different
key or API root, set `LLM_VISION_BASE_URL` and `LLM_VISION_API_KEY`; both fall
back to the text-model values when left blank. The image adapter supports
text-to-image and one reference image, and requests base64 image data so result
download never depends on a signed CDN URL.

If either provider is left blank, the app still boots and reports that role as
not ready through configuration diagnostics.

## Checks

    uv run ruff check .
    uv run pytest -q

## Layout

- pyproject.toml / uv.lock: dependencies and lock
- .env.example: empty configuration sample (no credentials)
- alembic.ini / migrations: schema migrations
- data/: runtime data dir (SQLite db + references/generated/tmp), created on boot
- tests/: full HTTP test suite against a migrated temporary SQLite + image dir
