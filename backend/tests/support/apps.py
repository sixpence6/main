from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from httpx import ASGITransport

from app.adapters.registry import AdapterRegistry
from app.config import Settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

TEST_ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
TEST_ALLOWED_ORIGINS = ["https://testserver"]


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
        "allowed_hosts": TEST_ALLOWED_HOSTS,
        "allowed_origins": TEST_ALLOWED_ORIGINS,
        "allow_loopback_http": True,
        "auth_cookie_secure": True,
        "workflow_role_policy": base.workflow_role_policy,
        "llm_adapter": "stub-llm",
        "llm_base_url": "https://llm.invalid",
        "llm_api_key": "sk-test-llm",
        "llm_model": "stub-model",
        "llm_vision_model": "stub-vision",
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
    from tests.support.auth import AuthClient, ensure_test_admin, login

    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        transport = ASGITransport(app=application)
        async with AuthClient(transport=transport, base_url="https://testserver") as client:
            await login(client)
            yield client
