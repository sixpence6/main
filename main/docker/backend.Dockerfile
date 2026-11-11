FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/backend/.venv/bin:$PATH"

WORKDIR /app/backend

COPY --from=builder /app/backend/.venv /app/backend/.venv
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec python -m app.production --host 0.0.0.0 --port 8000"]
