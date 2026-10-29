from __future__ import annotations

import base64
import json
from dataclasses import replace

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.base import (
    BriefData,
    OrganizeImage,
    OrganizeMessage,
    OrganizeRequest,
    ProviderError,
)
from app.adapters.openai_chat import OpenAIChatAdapter
from app.adapters.registry import build_default_registry
from app.config import RoleConfig, Settings
from app.services.config_status import build_config_status


def _role(image_model: str | None = None) -> RoleConfig:
    return RoleConfig(
        role="llm",
        adapter_id="openai-chat",
        base_url="https://llm.example/v1",
        api_key=SecretStr("sk-test-secret"),
        model="chat-model",
        timeout_seconds=30,
        image_model=image_model,
    )


def _adapter(handler, image_model: str | None = None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAIChatAdapter(_role(image_model), client), client


def _request() -> OrganizeRequest:
    return OrganizeRequest(
        brief=BriefData(product_description="红色苹果", image_size="1024x1024"),
        messages=(
            OrganizeMessage(role="user", content="做一个红色苹果主图"),
            OrganizeMessage(role="assistant", content="还需要确认用途吗？"),
            OrganizeMessage(role="user", content="电商主图，白色背景"),
        ),
        user_text="电商主图，白色背景",
        has_reference_image=False,
    )


def _image_request(data: bytes = b"\x89PNG\r\n", mime_type: str = "image/png"):
    return replace(
        _request(),
        has_reference_image=True,
        reference_image=OrganizeImage(data=data, mime_type=mime_type),
    )


def _completion(content: str) -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
            }
        ]
    }


def _review_payload(prompt: str = "红色苹果电商主图，白色背景，居中构图") -> str:
    return json.dumps(
        {
            "status": "review",
            "content": "需求已经整理好，请确认。",
            "fields": {
                "product_description": "红色苹果",
                "purpose": "电商主图",
                "style": "干净产品摄影",
                "composition": "居中构图，白色背景",
                "text_content": "",
            },
            "prompt": prompt,
        },
        ensure_ascii=False,
    )


async def test_organize_sends_chat_request_and_parses_review():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion(_review_payload()))

    adapter, client = _adapter(handler)
    try:
        result = await adapter.organize(_request())
    finally:
        await client.aclose()

    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test-secret"
    body = seen["body"]
    assert body["model"] == "chat-model"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1] == {"role": "user", "content": "电商主图，白色背景"}
    assert result.outcome == "review"
    assert result.assistant_message == "需求已经整理好，请确认。"
    assert result.brief.product_description == "红色苹果"
    assert result.brief.image_size == "1024x1024"
    assert result.brief.prompt.startswith("红色苹果电商主图")


async def test_needs_input_accepts_partial_fields_and_ignores_prompt():
    content = json.dumps(
        {
            "status": "needs_input",
            "content": "请确认商品主体和用途。",
            "fields": {"product_description": "红色苹果", "purpose": None},
            "prompt": "不应该被使用",
        },
        ensure_ascii=False,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    adapter, client = _adapter(handler)
    try:
        result = await adapter.organize(_request())
    finally:
        await client.aclose()

    assert result.outcome == "needs_input"
    assert result.brief.product_description == "红色苹果"
    assert result.brief.purpose == ""
    assert result.brief.prompt == ""


async def test_json_fence_is_accepted():
    fence = chr(96) * 3
    content = "\n".join((fence + "json", _review_payload(), fence))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    adapter, client = _adapter(handler)
    try:
        result = await adapter.organize(_request())
    finally:
        await client.aclose()

    assert result.outcome == "review"
    assert result.brief.prompt


async def test_http_rejection_is_classified_without_leaking_credentials():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": (
                        "invalid api key sk-test-secret at "
                        "https://api.example/v1?token=signed-value"
                    )
                }
            },
        )

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "rejected"
    assert raised.value.error_code == "LLM_AUTH_FAILED"
    assert "sk-test-secret" not in raised.value.args[0]
    assert "https://" not in raised.value.args[0]


async def test_invalid_content_is_rejected_without_treating_it_as_a_brief():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("not json"))

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "uncertain"
    assert raised.value.error_code == "LLM_RESPONSE_INVALID"


async def test_review_requires_a_prompt():
    content = _review_payload(prompt="")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "rejected"
    assert raised.value.error_code == "LLM_RESPONSE_INVALID"


async def test_server_error_leaves_acceptance_uncertain():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "uncertain"
    assert raised.value.error_code == "LLM_UPSTREAM_ERROR"


async def test_timeout_leaves_acceptance_uncertain():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "uncertain"
    assert raised.value.error_code == "LLM_TIMEOUT"


async def test_review_requires_all_brief_fields():
    content = json.dumps(
        {
            "status": "review",
            "content": "可以确认。",
            "fields": {
                "product_description": "白色马克杯",
                "purpose": "电商主图",
                "style": "极简",
                "composition": "居中",
            },
            "prompt": "白色马克杯电商主图，极简风格，居中构图",
        },
        ensure_ascii=False,
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_request())
    finally:
        await client.aclose()

    assert raised.value.evidence == "rejected"
    assert raised.value.error_code == "LLM_RESPONSE_INVALID"


async def test_reference_image_is_sent_as_a_multimodal_content_part():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion(_review_payload()))

    image = b"\x89PNG\r\n\x1a\nreference"
    adapter, client = _adapter(handler, image_model="vision-model")
    try:
        result = await adapter.organize(_image_request(image))
    finally:
        await client.aclose()

    body = seen["body"]
    assert body["model"] == "vision-model"
    content = body["messages"][-1]["content"]
    assert content[0] == {"type": "text", "text": "电商主图，白色背景"}
    assert content[1]["type"] == "image_url"
    expected = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    assert content[1]["image_url"]["url"] == expected
    assert result.outcome == "review"


async def test_reference_image_without_vision_model_is_rejected_before_http():
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_completion(_review_payload()))

    adapter, client = _adapter(handler)
    try:
        with pytest.raises(ProviderError) as raised:
            await adapter.organize(_image_request())
    finally:
        await client.aclose()

    assert called is False
    assert raised.value.evidence == "rejected"
    assert raised.value.error_code == "LLM_VISION_NOT_CONFIGURED"


def test_builtin_registry_exposes_openai_chat_and_config_is_ready():
    registry = build_default_registry()
    assert "openai-chat" in registry.known_llm_ids()

    settings = Settings(
        _env_file=None,
        llm_adapter="openai-chat",
        llm_base_url="https://llm.example/v1",
        llm_api_key="sk-test",
        llm_model="chat-model",
    )
    status = build_config_status(settings, registry)
    assert status.llm.configured is True
    assert status.llm.ready is True


def test_adapter_requires_configured_role():
    role = RoleConfig(
        role="llm",
        adapter_id="openai-chat",
        base_url=None,
        api_key=None,
        model=None,
        timeout_seconds=30,
    )
    with pytest.raises(ValueError):
        OpenAIChatAdapter(role, httpx.AsyncClient())
