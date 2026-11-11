from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import RateLimitFact
from app.auth.sessions import parse_timestamp
from app.db import write_transaction
from app.timeutil import format_timestamp

MAX_BUCKETS_PER_CLASS = 20_000

BucketClass = Literal[
    "login_account",
    "login_source",
    "link_issue_admin",
    "link_issue_target",
    "link_verify_credential",
    "link_verify_source",
]


@dataclass(frozen=True)
class LimitRule:
    bucket_class: BucketClass
    bucket_key: str
    limit: int
    window_seconds: int


class RateLimited(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate limited")
        self.retry_after_seconds = max(1, retry_after_seconds)


def _window_start(now: datetime, window_seconds: int) -> str:
    return format_timestamp(now - timedelta(seconds=window_seconds))


async def reserve_attempts(
    factory: async_sessionmaker[AsyncSession], rules: list[LimitRule], now: datetime
) -> list[int]:
    now_text = format_timestamp(now)
    async with write_transaction(factory) as session:
        for rule in rules:
            await session.execute(
                delete(RateLimitFact).where(
                    RateLimitFact.bucket_class == rule.bucket_class,
                    RateLimitFact.expires_at <= now_text,
                )
            )
        for rule in rules:
            total = await session.scalar(
                select(func.coalesce(func.sum(RateLimitFact.weight), 0)).where(
                    RateLimitFact.bucket_class == rule.bucket_class,
                    RateLimitFact.bucket_key == rule.bucket_key,
                    RateLimitFact.occurred_at > _window_start(now, rule.window_seconds),
                    RateLimitFact.occurred_at <= now_text,
                )
            )
            if int(total or 0) >= rule.limit:
                earliest = await session.scalar(
                    select(func.min(RateLimitFact.expires_at)).where(
                        RateLimitFact.bucket_class == rule.bucket_class,
                        RateLimitFact.bucket_key == rule.bucket_key,
                        RateLimitFact.occurred_at > _window_start(now, rule.window_seconds),
                        RateLimitFact.occurred_at <= now_text,
                    )
                )
                retry = rule.window_seconds
                if earliest:
                    retry = int((parse_timestamp(earliest) - now).total_seconds())
                raise RateLimited(retry)
        for rule in rules:
            distinct = await session.scalar(
                select(func.count(func.distinct(RateLimitFact.bucket_key))).where(
                    RateLimitFact.bucket_class == rule.bucket_class,
                    RateLimitFact.expires_at > now_text,
                )
            )
            if int(distinct or 0) >= MAX_BUCKETS_PER_CLASS:
                existing = await session.scalar(
                    select(func.count()).where(
                        RateLimitFact.bucket_class == rule.bucket_class,
                        RateLimitFact.bucket_key == rule.bucket_key,
                        RateLimitFact.expires_at > now_text,
                    )
                )
                if not existing:
                    raise RateLimited(rule.window_seconds)
        facts = []
        for rule in rules:
            fact = RateLimitFact(
                bucket_class=rule.bucket_class,
                bucket_key=rule.bucket_key,
                occurred_at=now_text,
                expires_at=format_timestamp(now + timedelta(seconds=rule.window_seconds)),
                weight=1,
                outcome="pending",
            )
            session.add(fact)
            facts.append(fact)
        await session.flush()
        return [fact.id for fact in facts]


async def settle_attempts_in_session(
    session: AsyncSession, fact_ids: list[int], *, success: bool
) -> None:
    if not fact_ids:
        return
    if success:
        await session.execute(delete(RateLimitFact).where(RateLimitFact.id.in_(fact_ids)))
    else:
        await session.execute(
            update(RateLimitFact)
            .where(RateLimitFact.id.in_(fact_ids))
            .values(outcome="failure")
        )


async def settle_attempts(
    factory: async_sessionmaker[AsyncSession], fact_ids: list[int], *, success: bool
) -> None:
    if not fact_ids:
        return
    async with write_transaction(factory) as session:
        await settle_attempts_in_session(session, fact_ids, success=success)
