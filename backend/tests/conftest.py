from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport

BACKEND_DIR = Path(__file__).resolve().parent.parent

MAIN_BACKEND_DIR = BACKEND_DIR.parent / "main" / "backend"
IMPORT_ROOT = MAIN_BACKEND_DIR if MAIN_BACKEND_DIR.is_dir() else BACKEND_DIR
if str(IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(IMPORT_ROOT))

from app.adapters.registry import AdapterRegistry  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

TEST_ALLOWED_HOSTS = ["testserver", "127.0.0.1", "localhost"]
TEST_ALLOWED_ORIGINS = ["https://testserver"]


def migrate(settings: Settings) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


def _test_settings(base: Settings, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "data_dir": base.data_dir,
        "web_dist_dir": base.web_dist_dir,
        "allowed_hosts": TEST_ALLOWED_HOSTS,
        "allowed_origins": TEST_ALLOWED_ORIGINS,
        "allow_loopback_http": True,
        "auth_cookie_secure": True,
        "app_public_url": "https://testserver",
        "llm_adapter": None,
        "llm_base_url": None,
        "llm_api_key": None,
        "llm_model": None,
        "visual_adapter": None,
        "visual_base_url": None,
        "visual_api_key": None,
        "visual_model": None,
        "visual_image_size": None,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        web_dist_dir=tmp_path / "web",
        allowed_hosts=TEST_ALLOWED_HOSTS,
        allowed_origins=TEST_ALLOWED_ORIGINS,
        allow_loopback_http=True,
        auth_cookie_secure=True,
        app_public_url="https://testserver",
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
    return _test_settings(
        settings,
        llm_adapter="stub-llm",
        llm_base_url="https://llm.invalid",
        llm_api_key="sk-test-llm",
        llm_model="stub-model",
        llm_vision_model="stub-vision",
        llm_timeout_seconds=5,
    )


@pytest.fixture
def full_settings(settings: Settings) -> Settings:
    return _test_settings(
        settings,
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
    from tests.support.auth import ensure_test_admin

    migrate(llm_settings)
    application = create_app(settings=llm_settings, registry=build_test_registry())
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        yield application


@pytest_asyncio.fixture
async def llm_client(llm_app):
    from tests.support.auth import login

    transport = ASGITransport(app=llm_app)
    from tests.support.auth import AuthClient

    async with AuthClient(transport=transport, base_url="https://testserver") as http_client:
        await login(http_client)
        yield http_client


@pytest_asyncio.fixture
async def app(settings: Settings, registry: AdapterRegistry):
    from tests.support.auth import ensure_test_admin

    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        yield application


@pytest_asyncio.fixture
async def client(app):
    from tests.support.auth import AuthClient, login

    transport = ASGITransport(app=app)
    async with AuthClient(transport=transport, base_url="https://testserver") as http_client:
        await login(http_client)
        yield http_client


@pytest_asyncio.fixture
async def anonymous_client(app):
    from tests.support.auth import AnonymousClient

    transport = ASGITransport(app=app)
    async with AnonymousClient(transport=transport, base_url="https://testserver") as http_client:
        yield http_client
