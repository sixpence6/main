from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


def build_dsn(settings: Settings) -> str:
    return settings.database_dsn


def create_engine(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(build_dsn(settings), future=True, connect_args={"timeout": 5.0})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def read_session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


@asynccontextmanager
async def write_transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Short write transaction. BEGIN IMMEDIATE serialises writers at the SQLite level."""
    async with factory() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def check_database_writable(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("BEGIN IMMEDIATE"))
        current = (await connection.execute(text("PRAGMA user_version"))).scalar_one()
        await connection.execute(text(f"PRAGMA user_version = {int(current)}"))
        await connection.rollback()


async def check_database_readable(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


class ProjectLocks:
    """In-process, non-blocking per-project lock used to reject concurrent writes early."""

    def __init__(self) -> None:
        self._owners: set[str] = set()
        self._guard = asyncio.Lock()

    async def acquire(self, project_id: str) -> bool:
        async with self._guard:
            if project_id in self._owners:
                return False
            self._owners.add(project_id)
            return True

    def release(self, project_id: str) -> None:
        self._owners.discard(project_id)

    @asynccontextmanager
    async def hold(self, project_id: str):
        acquired = await self.acquire(project_id)
        if not acquired:
            raise _project_busy()
        try:
            yield
        finally:
            self.release(project_id)


def _project_busy():
    from app.errors import conflict

    return conflict("PROJECT_BUSY", "项目正在处理其他写操作，请稍后重试")
