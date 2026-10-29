from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.errors import StartupError
from app.main import create_app


async def test_health_reports_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_empty_config_status_is_safe_and_explicit(client):
    response = await client.get("/api/config/status")
    assert response.status_code == 200
    body = response.json()
    for role in ("llm", "visual"):
        status = body[role]
        assert status["configured"] is False
        assert status["ready"] is False
        assert status["verification"] == "not_checked"
        assert set(status["missing_fields"]) == {"adapter", "base_url", "api_key", "model"}
        assert status["timeout_seconds"] > 0
    assert body["visual"]["capabilities"]["text_to_image"] is None
    assert body["visual"]["capabilities"]["reference_image"] is None
    assert body["visual"]["capabilities"]["supported_image_sizes"] is None
    assert body["visual"]["capabilities"]["effective_reference_max_bytes"] == 10_000_000


async def test_config_status_never_leaks_credentials(settings, registry):
    secret_settings = Settings(
        _env_file=None,
        data_dir=settings.data_dir,
        web_dist_dir=settings.web_dist_dir,
        llm_adapter="stub-llm",
        llm_base_url="https://llm.internal.example/v1",
        llm_api_key="sk-test-super-secret-value",
        llm_model="llm-model-x",
        visual_adapter="stub-visual",
        visual_base_url="https://visual.internal.example/v1",
        visual_api_key="sk-test-super-secret-value",
        visual_model="visual-model-x",
    )
    application = create_app(settings=secret_settings, registry=registry)
    from tests.conftest import migrate

    migrate(secret_settings)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            response = await http_client.get("/api/config/status")
    assert response.status_code == 200
    assert "sk-test-super-secret-value" not in response.text
    assert "internal.example" not in response.text
    for role in ("llm", "visual"):
        status = response.json()[role]
        assert status["configured"] is True
        assert status["ready"] is True
        assert status["verification"] == "not_checked"
        assert status["model"] in {"llm-model-x", "visual-model-x"}


async def test_unknown_api_route_returns_json_404(client):
    response = await client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "ROUTE_NOT_FOUND", "message": "接口不存在"}}


async def test_missing_web_build_is_reported(app, client):
    response = await client.get("/")
    assert response.status_code == 503
    assert "npm --prefix main run build" in response.text


async def test_workspace_is_served_from_configured_dist(app, settings, client):
    dist = settings.resolved_web_dist_dir()
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<html><body>workspace</body></html>", encoding="utf-8")
    (dist / "assets").mkdir(exist_ok=True)
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    root = await client.get("/")
    assert root.status_code == 200
    assert "workspace" in root.text
    asset = await client.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.text == "console.log(1)"


async def test_startup_fails_without_migrations(settings):
    from app.main import create_app as build_app

    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    application = build_app(settings=settings)
    with pytest.raises(StartupError):
        async with application.router.lifespan_context(application):
            pass


async def test_read_only_configuration_makes_zero_model_calls(app, registry, client):
    response = await client.get("/api/config/status")
    assert response.status_code == 200
    assert registry.recorder.llm_builds == 0
    assert registry.recorder.visual_builds == 0
    assert registry.stub_llm.calls == []
    assert registry.stub_visual.submit_calls == []
