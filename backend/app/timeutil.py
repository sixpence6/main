from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def format_timestamp(value: datetime | None = None) -> str:
    """Return an ISO-8601 UTC timestamp with millisecond precision and a Z suffix."""
    moment = (value or utc_now()).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
