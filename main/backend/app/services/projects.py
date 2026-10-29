from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import read_session, write_transaction
from app.errors import ApiError, not_found_project
from app.models import Asset, Generation, Message, Project
from app.schemas import ProjectSummary
from app.timeutil import format_timestamp, utc_now

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100
DEFAULT_PROJECT_TITLE = "未命名项目"
CURSOR_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass(frozen=True)
class ProjectCursor:
    updated_at: str
    project_id: str


def encode_cursor(updated_at: str, project_id: str) -> str:
    payload = json.dumps(
        {"id": project_id, "updated_at": updated_at},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _invalid_cursor() -> ApiError:
    return ApiError(422, "INVALID_CURSOR", "分页游标无效，请重新加载列表")


def decode_cursor(raw: str | None) -> ProjectCursor | None:
    if raw is None or raw == "":
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError):
        raise _invalid_cursor() from None
    if not isinstance(payload, dict) or set(payload) != {"id", "updated_at"}:
        raise _invalid_cursor()
    updated_at = payload["updated_at"]
    project_id = payload["id"]
    if not isinstance(updated_at, str) or not isinstance(project_id, str):
        raise _invalid_cursor()
    try:
        datetime.strptime(updated_at, CURSOR_TIME_FORMAT)
        UUID(project_id)
    except ValueError:
        raise _invalid_cursor() from None
    return ProjectCursor(updated_at=updated_at, project_id=project_id)


async def create_project(session_factory: async_sessionmaker[AsyncSession]) -> Project:
    now = format_timestamp(utc_now())
    project = Project(
        id=str(uuid4()),
        title=DEFAULT_PROJECT_TITLE,
        revision=0,
        prompt_revision=None,
        confirmed_revision=None,
        brief_status="draft",
        product_description="",
        purpose="",
        style="",
        composition="",
        text_content="",
        image_size=None,
        prompt="",
        reference_asset_id=None,
        organize_operation_id=None,
        brief_error_code=None,
        brief_error_message=None,
        created_at=now,
        updated_at=now,
    )
    async with write_transaction(session_factory) as session:
        session.add(project)
        await session.flush()
    return project


async def _latest_generation_statuses(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, str]:
    if not project_ids:
        return {}
    rows = await session.execute(
        select(Generation.project_id, Generation.status, Generation.version_no)
        .where(Generation.project_id.in_(project_ids))
        .order_by(Generation.project_id, Generation.version_no.desc())
    )
    latest: dict[str, str] = {}
    for project_id, status, _version_no in rows:
        latest.setdefault(project_id, status)
    return latest


async def list_projects(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    limit: int,
    cursor: ProjectCursor | None,
) -> tuple[list[ProjectSummary], str | None]:
    async with read_session(session_factory) as session:
        statement = (
            select(Project)
            .order_by(Project.updated_at.desc(), Project.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    Project.updated_at < cursor.updated_at,
                    and_(
                        Project.updated_at == cursor.updated_at,
                        Project.id < cursor.project_id,
                    ),
                )
            )
        projects = list((await session.scalars(statement)).all())
        page = projects[:limit]
        latest = await _latest_generation_statuses(session, [project.id for project in page])

    next_cursor = None
    if len(projects) > limit and page:
        next_cursor = encode_cursor(page[-1].updated_at, page[-1].id)
    items = [
        ProjectSummary(
            id=project.id,
            title=project.title,
            brief_status=project.brief_status,  # type: ignore[arg-type]
            latest_generation_status=latest.get(project.id),  # type: ignore[arg-type]
            updated_at=project.updated_at,
        )
        for project in page
    ]
    return items, next_cursor


@dataclass
class ProjectDetail:
    project: Project
    messages: list[Message]
    generations: list[Generation]
    reference_asset: Asset | None
    result_assets: dict[str, Asset]


async def get_project_detail(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> ProjectDetail:
    async with read_session(session_factory) as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise not_found_project()
        messages = list(
            (
                await session.scalars(
                    select(Message)
                    .where(Message.project_id == project_id)
                    .order_by(Message.sequence_no)
                )
            ).all()
        )
        generations = list(
            (
                await session.scalars(
                    select(Generation)
                    .where(Generation.project_id == project_id)
                    .order_by(Generation.version_no)
                )
            ).all()
        )
        reference_asset = (
            await session.get(Asset, project.reference_asset_id)
            if project.reference_asset_id
            else None
        )
        result_ids = sorted(
            {generation.result_asset_id for generation in generations if generation.result_asset_id}
        )
        result_assets: dict[str, Asset] = {}
        if result_ids:
            for asset in await session.scalars(select(Asset).where(Asset.id.in_(result_ids))):
                result_assets[asset.id] = asset
    return ProjectDetail(
        project=project,
        messages=messages,
        generations=generations,
        reference_asset=reference_asset,
        result_assets=result_assets,
    )


async def require_project(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise not_found_project()
    return project
