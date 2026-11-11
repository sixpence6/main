from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

#: A request or background task that may commit business rows.
BUSINESS_WRITE = "business_write"
#: A write of an immutable reference/generated/published file.
FILE_PUBLICATION = "file_publication"
#: Retention cleanup that may unlink expired candidate files or rows.
EXPIRY_DELETION = "expiry_deletion"

CHANNELS = frozenset({BUSINESS_WRITE, FILE_PUBLICATION, EXPIRY_DELETION})


class MaintenanceError(RuntimeError):
    """Base class for maintenance coordination failures."""

    code = "MAINTENANCE_ERROR"


class MaintenanceBlocked(MaintenanceError):
    """Raised when a new write starts while a maintenance window is open."""

    code = "MAINTENANCE_ACTIVE"

    def __init__(self, channel: str) -> None:
        super().__init__(f"maintenance is active; new {channel} operations are blocked")
        self.channel = channel


class MaintenanceAlreadyActive(MaintenanceError):
    """Raised when a second maintenance window is requested concurrently."""

    code = "MAINTENANCE_ALREADY_ACTIVE"


class MaintenanceTimeout(MaintenanceError):
    """Raised when in-flight writes do not drain inside the configured timeout."""

    code = "MAINTENANCE_TIMEOUT"

    def __init__(self, timeout: float, in_flight: dict[str, int]) -> None:
        super().__init__(f"maintenance could not drain in-flight writes within {timeout:g}s")
        self.timeout = timeout
        self.in_flight = dict(in_flight)


@dataclass(frozen=True)
class MaintenanceState:
    active: bool
    in_flight: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.in_flight.values())


@dataclass(frozen=True)
class MaintenanceSession:
    """The exclusive maintenance window; at this point the drain is complete."""

    started_at: float
    state: MaintenanceState


class MaintenanceCoordinator:
    """In-process gate for business writes, file publication and expiry deletion.

    Writers hold a :meth:`guard` lease. :meth:`maintenance` flips the gate to
    active first (new leases are rejected immediately), then waits for every
    existing lease to drain. A timeout exits with :class:`MaintenanceTimeout`
    and always restores the open state so a failed backup cannot leave the
    service permanently read-only.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active = False
        self._in_flight: dict[str, int] = {}
        self._publication_condition = threading.Condition()
        self._file_publications = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def in_flight(self) -> dict[str, int]:
        return dict(self._in_flight)

    def state(self) -> MaintenanceState:
        return MaintenanceState(active=self._active, in_flight=dict(self._in_flight))

    @asynccontextmanager
    async def guard(self, channel: str) -> AsyncIterator[None]:
        """Register one in-flight write for the duration of the context."""
        if channel not in CHANNELS:
            raise ValueError(f"unknown maintenance channel: {channel}")
        async with self._condition:
            if self._active:
                raise MaintenanceBlocked(channel)
            self._in_flight[channel] = self._in_flight.get(channel, 0) + 1
        try:
            yield
        finally:
            async with self._condition:
                remaining = self._in_flight.get(channel, 0) - 1
                if remaining > 0:
                    self._in_flight[channel] = remaining
                else:
                    self._in_flight.pop(channel, None)
                self._condition.notify_all()

    @contextmanager
    def file_publication(self) -> Iterator[None]:
        """Hold a synchronous lease for one immutable-file publication.

        File publication runs in worker threads. The lease is tracked under a
        thread condition so :meth:`maintenance` can wait for a write that
        already passed the gate; a writer that arrives after the gate closes is
        rejected before it creates any file.
        """
        with self._publication_condition:
            if self._active:
                raise MaintenanceBlocked(FILE_PUBLICATION)
            self._file_publications += 1
        try:
            yield
        finally:
            with self._publication_condition:
                self._file_publications -= 1
                if not self._file_publications:
                    self._publication_condition.notify_all()

    @asynccontextmanager
    async def maintenance(  # noqa: ASYNC109
        self, *, timeout: float | None = 30.0  # noqa: ASYNC109
    ) -> AsyncIterator[MaintenanceSession]:
        """Open an exclusive window after all in-flight writes have drained."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        async with self._condition:
            if self._active:
                raise MaintenanceAlreadyActive()
            self._active = True
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            await self._wait_for_drain(timeout, deadline)
            await asyncio.to_thread(self._wait_for_file_publications, timeout, deadline)
            yield MaintenanceSession(started_at=time.monotonic(), state=self.state())
        finally:
            async with self._condition:
                self._active = False
                self._condition.notify_all()

    async def _wait_for_drain(  # noqa: ASYNC109
        self, timeout: float | None, deadline: float | None  # noqa: ASYNC109
    ) -> None:
        async with self._condition:
            while self._in_flight:
                if deadline is None:
                    await self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MaintenanceTimeout(
                        timeout=timeout or 0.0, in_flight=dict(self._in_flight)
                    )
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    raise MaintenanceTimeout(
                        timeout=timeout or 0.0, in_flight=dict(self._in_flight)
                    ) from None

    def _wait_for_file_publications(self, timeout: float | None, deadline: float | None) -> None:
        with self._publication_condition:
            while self._file_publications:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise MaintenanceTimeout(
                        timeout=timeout or 0.0,
                        in_flight={FILE_PUBLICATION: self._file_publications},
                    )
                if not self._publication_condition.wait(remaining):
                    raise MaintenanceTimeout(
                        timeout=timeout or 0.0,
                        in_flight={FILE_PUBLICATION: self._file_publications},
                    )


_ACTIVE_COORDINATOR: MaintenanceCoordinator | None = None


def set_active_coordinator(coordinator: MaintenanceCoordinator | None) -> None:
    """Install the coordinator used by the running application gate."""
    global _ACTIVE_COORDINATOR
    _ACTIVE_COORDINATOR = coordinator


def get_active_coordinator() -> MaintenanceCoordinator | None:
    return _ACTIVE_COORDINATOR


@contextmanager
def file_publication_guard() -> Iterator[None]:
    """Apply the active coordinator's synchronous file-publication lease."""
    coordinator = _ACTIVE_COORDINATOR
    if coordinator is None:
        yield
        return
    with coordinator.file_publication():
        yield


__all__ = [
    "BUSINESS_WRITE",
    "CHANNELS",
    "EXPIRY_DELETION",
    "FILE_PUBLICATION",
    "MaintenanceAlreadyActive",
    "MaintenanceBlocked",
    "MaintenanceCoordinator",
    "MaintenanceError",
    "MaintenanceSession",
    "MaintenanceState",
    "MaintenanceTimeout",
    "file_publication_guard",
    "get_active_coordinator",
    "set_active_coordinator",
]
