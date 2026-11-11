from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import Account, AuthSession
from app.auth.principal import Principal
from app.db import read_session
from app.errors import ApiError, not_found_project
from app.models import Project


def _auth_required() -> ApiError:
    return ApiError(401, "AUTH_REQUIRED", "请先登录")


async def revalidate_principal(session: AsyncSession, principal: Principal) -> Account:
    """Re-read the account and session inside the caller's transaction.

    Middleware already checked the cookie; this guard closes the window where
    an account is disabled or all sessions are revoked between that check and
    the business read/write.
    """
    account = await session.get(Account, principal.account_id)
    if account is None or not account.can_login:
        raise _auth_required()
    if account.session_epoch != principal.account_epoch:
        raise _auth_required()
    auth_session = await session.get(AuthSession, principal.session_id)
    if (
        auth_session is None
        or auth_session.account_id != principal.account_id
        or auth_session.revoked_at is not None
        or auth_session.account_epoch != principal.account_epoch
    ):
        raise _auth_required()
    return account


async def require_owned_project(
    session: AsyncSession, project_id: str, principal: Principal
) -> Project:
    await revalidate_principal(session, principal)
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.owner_account_id == principal.account_id,
        )
    )
    if project is None:
        # Other owner, unknown ID and unowned legacy row are intentionally
        # indistinguishable: no existence oracle through the public API.
        raise not_found_project()
    return project


async def require_project_scope(
    session: AsyncSession, project_id: str, principal: Principal | None
) -> Project:
    if principal is None:
        project = await session.get(Project, project_id)
        if project is None:
            raise not_found_project()
        return project
    return await require_owned_project(session, project_id, principal)


async def require_project_owner(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: str,
    principal: Principal,
) -> Project:
    async with read_session(session_factory) as session:
        return await require_owned_project(session, project_id, principal)
