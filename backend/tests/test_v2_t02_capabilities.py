from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.adapters.base import ImageCapabilities
from app.config import Settings
from app.errors import ApiError
from app.main import create_app
from app.workflow import state
from app.workflow.roles import run_role
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _migrate(settings: Settings) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


@asynccontextmanager
async def _client_for(settings: Settings, registry=None):
    """真实应用生命周期与临时 SQLite；只替换外部模型适配器。"""
    _migrate(settings)
    application = create_app(settings=settings, registry=registry or build_test_registry())
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client


def _capabilities(body: dict, name: str) -> dict:
    value = body.get(name)
    assert isinstance(value, dict), f"缺少 {name} 能力状态: {body}"
    return value


async def test_config_status_separates_three_capabilities_with_real_defaults(client):
    """AC-44/AC-45：空配置仍分别表达 TEXT、VISION、IMAGE_GEN 与真实等待上限。"""
    response = await client.get("/api/config/status")
    assert response.status_code == 200, response.text
    body = response.json()

    text = _capabilities(body, "text")
    vision = _capabilities(body, "vision")
    image_generation = _capabilities(body, "image_generation")
    gate = body["generation"]

    assert text["capability"] == "TEXT"
    assert vision["capability"] == "VISION"
    assert image_generation["capability"] == "IMAGE_GEN"
    assert text["timeout_seconds"] == 60
    assert vision["timeout_seconds"] == 60
    assert image_generation["timeout_seconds"] == 300
    assert text["configured"] is False and text["ready"] is False
    assert vision["configured"] is False and vision["ready"] is False
    assert image_generation["configured"] is False and image_generation["ready"] is False
    assert text["verification"] == vision["verification"] == "not_checked"
    assert image_generation["verification"] == "not_checked"
    assert gate == {
        "allowed": False,
        "missing_capabilities": ["TEXT", "VISION", "IMAGE_GEN"],
        "issues": gate["issues"],
        "verification": "not_checked",
    }
    assert [issue["code"] for issue in gate["issues"]] == [
        "TEXT_NOT_READY",
        "VISION_NOT_READY",
        "IMAGE_GEN_NOT_READY",
    ]
    # 不虚构供应商、模型或尺寸；空配置必须保持空值。
    assert text["adapter_id"] is None and text["model"] is None
    assert vision["adapter_id"] is None and vision["model"] is None
    assert image_generation["adapter_id"] is None and image_generation["model"] is None
    assert image_generation["capabilities"]["selected_image_size"] is None
    assert image_generation["capabilities"]["usable_image_size"] is None


async def test_text_only_config_collects_requirements_but_blocks_paid_generation(llm_settings):
    """AC-45：仅 TEXT 可收集需求；未配置 IMAGE_GEN 时付费生图门禁明确关闭。"""
    registry = build_test_registry()
    async with _client_for(llm_settings, registry) as (_application, client):
        response = await client.get("/api/config/status")
        assert response.status_code == 200, response.text
        body = response.json()

        text = _capabilities(body, "text")
        vision = _capabilities(body, "vision")
        image_generation = _capabilities(body, "image_generation")

        assert text["configured"] is True and text["ready"] is True
        assert text["model"] == "stub-model"
        assert vision["configured"] is True and vision["ready"] is True
        assert vision["model"] == "stub-vision"
        # VISION 是独立能力，不能因为复用 LLM 配置就丢失显式模型标识。
        assert vision["model"] != text["model"]
        assert vision["endpoint_source"] == "shared_text"
        assert vision["credential_source"] == "shared_text"
        assert image_generation["configured"] is False
        assert image_generation["ready"] is False
        assert body["generation"]["allowed"] is False
        assert body["generation"]["missing_capabilities"] == ["IMAGE_GEN"]

        text_fp = text["model_fingerprint"]
        vision_fp = vision["model_fingerprint"]
        assert text_fp and text_fp.startswith("stub-llm:stub-model:")
        assert vision_fp and vision_fp.startswith("stub-llm:stub-vision:")
        assert text_fp != vision_fp
        assert "sk-test-llm" not in response.text
        assert "llm.invalid" not in response.text


async def test_missing_vision_blocks_paid_generation_and_provider_calls(full_settings):
    """AC-45：TEXT+IMAGE_GEN 但缺 VISION 时，API 门禁和角色调用都在付费请求前阻断。"""
    llm = LLMStub()
    visual = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    settings = full_settings.model_copy(update={"llm_vision_model": None})

    async with _client_for(settings, registry) as (application, client):
        response = await client.get("/api/config/status")
        assert response.status_code == 200, response.text
        body = response.json()

        text = _capabilities(body, "text")
        vision = _capabilities(body, "vision")
        image_generation = _capabilities(body, "image_generation")

        assert text["ready"] is True
        assert vision["configured"] is False and vision["ready"] is False
        assert "vision_model" in vision["missing_fields"]
        assert image_generation["ready"] is True
        assert body["generation"]["allowed"] is False
        assert body["generation"]["missing_capabilities"] == ["VISION"]
        assert [issue["code"] for issue in body["generation"]["issues"]] == ["VISION_NOT_READY"]
        # IMAGE_GEN 已配置也不能掩盖 VISION 缺失。
        assert image_generation["configured"] is True

        assert registry.recorder.llm_builds == 0
        assert registry.recorder.visual_builds == 0
        assert llm.calls == []
        assert visual.submit_calls == []

        project = (await client.post("/api/projects", json={})).json()
        ctx = await state.load_context(application, project["id"])
        await state.create_run(ctx)
        ctx = await state.reload_context(ctx)
        with pytest.raises(ApiError) as raised:
            await run_role(ctx, "visual_director", "S08", {"task": "生成图片"}, capability="IMAGE_GEN")
        assert raised.value.status_code == 503
        assert raised.value.code == "CAPABILITY_NOT_READY"
        assert llm.calls == []
        assert llm.role_calls_received == []
        assert visual.submit_calls == []


async def test_shared_vision_configuration_never_leaks_shared_credentials(llm_settings):
    """AC-44：共享 TEXT 地址/凭据时只展示来源与模型，不泄露密钥。"""
    async with _client_for(llm_settings) as (_application, client):
        response = await client.get("/api/config/status")
        body = response.json()
        vision = _capabilities(body, "vision")

        assert vision["configured"] is True and vision["ready"] is True
        assert vision["adapter_id"] == "stub-llm"
        assert vision["model"] == "stub-vision"
        assert vision["endpoint_source"] == "shared_text"
        assert vision["credential_source"] == "shared_text"
        assert response.text.find("sk-test-llm") == -1
        assert response.text.find("llm.invalid") == -1
        assert vision["model_fingerprint"].count("sk-") == 0


async def test_independent_vision_configuration_is_explicit_and_redacted(full_settings):
    """AC-44/AC-45：VISION 可独立配置地址和凭据，但响应只保留脱敏指纹。"""
    settings = full_settings.model_copy(
        update={
            "llm_vision_base_url": "https://vision.internal.example/v1",
            "llm_vision_api_key": SecretStr("sk-test-vision-secret"),
            "llm_vision_model": "stub-vision-independent",
        }
    )
    registry = build_test_registry()
    async with _client_for(settings, registry) as (_application, client):
        response = await client.get("/api/config/status")
        assert response.status_code == 200, response.text
        body = response.json()
        vision = _capabilities(body, "vision")

        assert vision["configured"] is True and vision["ready"] is True
        assert vision["model"] == "stub-vision-independent"
        assert vision["endpoint_source"] == "independent"
        assert vision["credential_source"] == "independent"
        assert vision["model_fingerprint"].startswith("stub-llm:stub-vision-independent:")
        for secret in ("sk-test-vision-secret", "sk-test-llm", "vision.internal.example", "llm.invalid"):
            assert secret not in response.text
        assert "https://" not in response.text


async def test_effective_limits_take_stricter_adapter_and_local_rules(full_settings):
    """AC-44：字节、格式和尺寸限制取本地与适配器声明中更严格者。"""
    visual = VisualStub(
        capabilities=ImageCapabilities(
            text_to_image=True,
            reference_image=True,
            asynchronous=True,
            supported_image_sizes=("1024x1024", "1536x1536"),
            reference_mime_types=("image/png", "image/webp"),
            provider_max_reference_bytes=3_000_000,
            poll_interval_seconds=0.01,
        )
    )
    registry = build_test_registry(visual=visual)
    async with _client_for(full_settings, registry) as (_application, client):
        response = await client.get("/api/config/status")
        assert response.status_code == 200, response.text
        body = response.json()
        image_generation = _capabilities(body, "image_generation")
        caps = image_generation["capabilities"]

        assert caps["provider_max_reference_bytes"] == 3_000_000
        assert caps["local_reference_max_bytes"] == 10_000_000
        assert caps["effective_reference_max_bytes"] == 3_000_000
        assert caps["reference_mime_types"] == ["image/png", "image/webp"]
        assert caps["effective_reference_mime_types"] == ["image/png"]
        assert caps["supported_image_sizes"] == ["1024x1024", "1536x1536"]
        assert caps["selected_image_size"] == "1024x1024"
        assert caps["usable_image_size"] == "1024x1024"
        assert body["generation"]["allowed"] is True


async def test_config_read_never_builds_adapters_or_claims_real_verification(full_settings):
    """AC-44/AC-45：读取配置只做静态判断，不触发付费探测，也不冒充真实核验。"""
    registry = build_test_registry()
    async with _client_for(full_settings, registry) as (_application, client):
        first = await client.get("/api/config/status")
        second = await client.get("/api/config/status")
        assert first.status_code == second.status_code == 200
        body = first.json()

        assert all(
            body[name]["verification"] == "not_checked"
            for name in ("llm", "visual", "text", "vision", "image_generation")
        )
        assert body["generation"]["verification"] == "not_checked"
        assert registry.recorder.llm_builds == 0
        assert registry.recorder.visual_builds == 0
        assert registry.stub_llm.calls == []
        assert registry.stub_visual.submit_calls == []
        assert first.text == second.text
