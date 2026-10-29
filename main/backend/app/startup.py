from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import REPO_ROOT, Settings
from app.db import check_database_writable
from app.errors import StartupError

BACKEND_DIR = REPO_ROOT / "backend"


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


def head_revision() -> str:
    script = ScriptDirectory.from_config(alembic_config())
    return script.get_current_head() or ""


def ensure_data_dir(settings: Settings) -> None:
    data_dir = settings.resolved_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        settings.assets_dir.mkdir(parents=True, exist_ok=True)
        settings.temp_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.temp_dir / ".write-probe"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        raise StartupError(f"数据目录不可用：{exc.strerror or exc}") from exc


async def verify_schema(engine: AsyncEngine) -> None:
    expected = head_revision()
    async with engine.connect() as connection:
        try:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 - surfaced as a startup failure
            raise StartupError(
                "数据库未完成迁移：缺少 alembic_version，请先运行 uv run alembic upgrade head"
            ) from exc
    if current != expected:
        raise StartupError(
            f"数据库迁移不完整：当前版本 {current or '无'}，期望 {expected}；"
            "请运行 uv run alembic upgrade head"
        )


async def prepare_database(engine: AsyncEngine) -> None:
    await verify_schema(engine)
    try:
        await check_database_writable(engine)
    except StartupError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StartupError("数据库不可写，请检查数据目录权限") from exc


def sync_database_url(settings: Settings) -> str:
    return settings.database_dsn.replace("+aiosqlite", "")
