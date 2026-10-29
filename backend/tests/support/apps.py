from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from app.adapters.registry import AdapterRegistry
from app.config import Settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


def migrate(settings: Settings) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


def llm_settings(base: Settings, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "data_dir": base.data_dir,
        "web_dist_dir": base.web_dist_dir,
        "llm_adapter": "stub-llm",
        "llm_base_url": "https://llm.invalid",
        "llm_api_key": "sk-test-llm",
        "llm_model": "stub-model",
        "llm_timeout_seconds": 5,
        "visual_adapter": None,
        "visual_base_url": None,
        "visual_api_key": None,
        "visual_model": None,
        "visual_image_size": None,
    }
    values.update(overrides)
    return Settings(**values)


@asynccontextmanager
async def run_client(settings: Settings, registry: AdapterRegistry):
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
