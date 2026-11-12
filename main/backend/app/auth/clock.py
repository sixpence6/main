from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.timeutil import utc_now


class Clock:
    """Server-side clock. Tests inject a FrozenClock through app.state.clock."""

    def now(self) -> datetime:
        return utc_now()


class FrozenClock(Clock):
    def __init__(self, current: datetime | None = None) -> None:
        self.current = (current or utc_now()).astimezone(UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)
