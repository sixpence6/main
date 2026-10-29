from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.base import BriefData, GenerationRequest, ProviderError
from app.adapters.openai_images import OpenAIImagesAdapter
from app.adapters.registry import build_default_registry
from app.config import RoleConfig, Settings
from app.services.config_status import build_config_status


def _role() -> RoleConfig:
    return RoleConfig(
        role="visual",
        adapter_id="openai-images",
        base_url="https://visual.example/v1",
        api_key=SecretStr("sk-test-secret"),
        model="image-model",
        timeout_seconds=30,
        image_size="1024x1024",
    )


def _adapter(handler) -> tuple[OpenAIImagesAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIImagesAdapter(_role(), client), client


def _request(reference_path: Path | None = None) -> GenerationRequest:
    return GenerationRequest(
        project_id="project-1",
        version_no=1,
        prompt="生成一张商品图",
        brief=BriefData(),
        image_size="1024x1024",
        reference_path=reference_path,
        reference_mime_type="image/png" if reference_path else None,
    )


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


async def test_text_to_image_requests_and_returns_base64():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"data": [{"b64_json": _b64(b"image-bytes")}]})

    adapter, client = _adapter(handler)
    try:
        outcome = await adapter.submit(_request())
    finally:
        await client.aclose()

    assert seen["path"] == "/v1/images/generations"
    assert seen["auth"] == "Bearer sk-test-secret"
    body = json.loads(str(seen["body"]))
    assert body["response_format"] == "b64_json"
    assert body["n"] == 1
    assert outcome.state == "succeeded"
    assert outcome.image_bytes == b"image-bytes"
    assert outcome.image_count == 1
    assert outcome.evidence == "result_available"


async def test_reference_image_uses_edits_endpoint(tmp_path: Path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference-bytes")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.read()
        seen["content_type"] = request.headers.get("content-type")
        return httpx.Response(200, json={"data": [{"b64_json": _b64(b"edited")} ]})

    adapter, client = _adapter(handler)
    try:
        outcome = await adapter.submit(_request(reference))
    finally:
        await client.aclose()

    assert seen["path"] == "/v1/images/edits"
    assert str(seen["content_type"]).startswith("multipart/form-data; boundary=")
    assert b'name="image"' in seen["body"]
    assert b'name="response_format"' in seen["body"]
    assert b"reference-bytes" in seen["body"]
    assert outcome.image_bytes == b"edited"


async def test_http_rejection_is_classified_without_leaking_credentials():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": (
                        "invalid api key for sk-test-secret at "
                        "https://api.example/v1?token=signed-value"
                    )
                }
            },
        )

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.submit(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "rejected"
    assert raised.value.error_code == "VISUAL_AUTH_FAILED"
    assert "sk-test-secret" not in raised.value.args[0]
    assert "https://" not in raised.value.args[0]


async def test_server_error_leaves_acceptance_uncertain():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.submit(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "uncertain"
    assert raised.value.error_code == "VISUAL_UPSTREAM_ERROR"


async def test_timeout_leaves_acceptance_uncertain():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.submit(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "uncertain"
    assert raised.value.error_code == "VISUAL_TIMEOUT"


async def test_invalid_json_is_uncertain():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.submit(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "uncertain"
    assert raised.value.error_code == "VISUAL_RESPONSE_INVALID"


async def test_multiple_results_are_rejected_explicitly():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"b64_json": _b64(b"one")},
                    {"b64_json": _b64(b"two")},
                ]
            },
        )

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.submit(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "result_available"
    assert raised.value.error_code == "VISUAL_MULTIPLE_IMAGES"


async def test_url_fallback_downloads_without_model_auth_and_follows_approved_redirect():
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        if request.url.host == "visual.example":
            return httpx.Response(
                302,
                headers={"location": "https://oss-us.dimenoss.com/result.png"},
            )
        return httpx.Response(200, content=b"downloaded-image")

    adapter, client = _adapter(handler)
    try:
        outcome = await adapter.fetch_result("https://visual.example/v1/result.png")
    finally:
        await client.aclose()

    assert outcome.state == "succeeded"
    assert outcome.image_bytes == b"downloaded-image"
    assert outcome.image_count == 1
    assert len(seen) == 2
    assert all(authorization is None for _, authorization in seen)


async def test_download_from_unapproved_host_is_rejected_before_request():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.fetch_result("https://evil.example/result.png")
    finally:
        await client.aclose()

    assert calls == 0
    assert raised.value.evidence == "result_available"
    assert raised.value.error_code == "RESULT_FETCH_FAILED"


def test_builtin_registry_exposes_openai_images_and_config_is_ready():
    registry = build_default_registry()
    assert "openai-images" in registry.known_visual_ids()
    capabilities = registry.visual_capabilities("openai-images")
    assert capabilities is not None
    assert capabilities.text_to_image is True
    assert capabilities.reference_image is True
    assert capabilities.asynchronous is False
    assert capabilities.supported_image_sizes == ("1024x1024",)

    settings = Settings(
        _env_file=None,
        visual_adapter="openai-images",
        visual_base_url="https://visual.example/v1",
        visual_api_key="sk-test",
        visual_model="image-model",
        visual_image_size="1024x1024",
    )
    status = build_config_status(settings, registry)
    assert status.visual.configured is True
    assert status.visual.ready is True
    assert status.visual.capabilities.text_to_image is True


def test_adapter_requires_configured_role():
    role = RoleConfig(
        role="visual",
        adapter_id="openai-images",
        base_url=None,
        api_key=None,
        model=None,
        timeout_seconds=30,
    )
    with pytest.raises(ValueError):
        OpenAIImagesAdapter(role, httpx.AsyncClient())
