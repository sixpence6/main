from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

BACKEND_DIR = Path(__file__).resolve().parent.parent

# httpx 0.28 cannot parse bracketed IPv6 entries in NO_PROXY (for example "[::1]").
# Normalize the test process environment before the app builds its shared client.
for _proxy_key in ("NO_PROXY", "no_proxy"):
    _proxy_value = os.environ.get(_proxy_key)
    if _proxy_value:
        os.environ[_proxy_key] = ",".join(
            part
            for part in _proxy_value.split(",")
            if not (part.startswith("[") and part.endswith("]"))
        )
MAIN_BACKEND_DIR = BACKEND_DIR.parent / "main" / "backend"
if str(MAIN_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(MAIN_BACKEND_DIR))

from app.adapters.registry import AdapterRegistry  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def migrate(settings: Settings) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        web_dist_dir=tmp_path / "web",
        llm_adapter=None,
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        visual_adapter=None,
        visual_base_url=None,
        visual_api_key=None,
        visual_model=None,
        visual_image_size=None,
    )


@pytest.fixture
def registry() -> AdapterRegistry:
    from tests.support.adapters import build_test_registry

    return build_test_registry()


@pytest.fixture
def llm_settings(settings: Settings) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=settings.data_dir,
        web_dist_dir=settings.web_dist_dir,
        llm_adapter="stub-llm",
        llm_base_url="https://llm.invalid",
        llm_api_key="sk-test-llm",
        llm_model="stub-model",
        llm_vision_model="stub-vision",
        llm_timeout_seconds=5,
        visual_adapter=None,
        visual_base_url=None,
        visual_api_key=None,
        visual_model=None,
        visual_image_size=None,
    )


@pytest.fixture
def full_settings(settings: Settings) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=settings.data_dir,
        web_dist_dir=settings.web_dist_dir,
        llm_adapter="stub-llm",
        llm_base_url="https://llm.invalid",
        llm_api_key="sk-test-llm",
        llm_model="stub-model",
        llm_vision_model="stub-vision",
        llm_timeout_seconds=5,
        visual_adapter="stub-visual",
        visual_base_url="https://visual.invalid",
        visual_api_key="sk-test-visual",
        visual_model="stub-model",
        visual_image_size="1024x1024",
        visual_timeout_seconds=5,
    )


@pytest_asyncio.fixture
async def llm_app(llm_settings: Settings):
    from tests.support.adapters import build_test_registry

    migrate(llm_settings)
    application = create_app(settings=llm_settings, registry=build_test_registry())
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def llm_client(llm_app):
    transport = ASGITransport(app=llm_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def app(settings: Settings, registry: AdapterRegistry):
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
