import json
from dataclasses import replace

import httpx
import pytest

from app.adapters.base import RoleRequest
from app.adapters.openai_chat import OpenAIChatAdapter
from app.config import Settings
from tests.test_openai_chat_adapter import (
    _completion,
    _image_request,
    _request,
    _review_payload,
    _role,
)


@pytest.mark.parametrize("mode", ["auto", "disabled", "enabled"])
async def test_configured_thinking_mode_reaches_text_role_requests(mode):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"role":"requirements_assistant","status":"ready","data":{}}'))

    settings = Settings(_env_file=None, llm_adapter="openai-chat", llm_model="deepseek-v4.1-flash",
                        llm_base_url="https://llm.example/v1", llm_api_key="test", llm_thinking_mode=mode)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIChatAdapter(settings.llm(), client).complete(RoleRequest(system_prompt="test", payload={}))
    if mode == "auto":
        assert "thinking" not in seen[0]
    else:
        assert seen[0]["thinking"] == {"type": mode}
    assert len(seen) == 1 and result.payload["status"] == "ready"


async def test_thinking_mode_does_not_leak_to_independent_vision_model():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"role":"reference_analyst","status":"ready","data":{}}'))

    role = replace(_role(vision_model="separate-vision"), thinking_mode="disabled")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAIChatAdapter(role, client).complete(RoleRequest(system_prompt="test", payload={}, images=(_image_request().reference_image,)))
    assert "thinking" not in seen[0]


async def test_legacy_organizer_also_applies_text_thinking_setting():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(_review_payload()))

    role = replace(_role(), thinking_mode="disabled")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAIChatAdapter(role, client).organize(_request())
    assert seen[0]["thinking"] == {"type": "disabled"}


async def test_disabled_thinking_timeout_is_not_automatically_retried():
    from app.adapters.base import ProviderError

    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        raise httpx.ReadTimeout("controlled timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAIChatAdapter(replace(_role(), thinking_mode="disabled"), client)
        with pytest.raises(ProviderError) as error:
            await adapter.complete(RoleRequest(system_prompt="test", payload={}))
    assert error.value.error_code == "LLM_TIMEOUT" and attempts == 1


async def test_thinking_mode_changes_only_its_effective_model_fingerprint():
    async with httpx.AsyncClient() as client:
        adapter = OpenAIChatAdapter(_role(), client)
        auto = adapter._fingerprint("chat-model", "https://llm.example/v1")
        disabled = adapter._fingerprint("chat-model", "https://llm.example/v1", "disabled")
    assert auto != disabled


@pytest.mark.parametrize("vision", [False, True])
async def test_medium_effort_reaches_every_role_model(vision):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"ok":true}'))

    settings = Settings(_env_file=None, llm_adapter="openai-chat", llm_base_url="https://llm.example/v1",
                        llm_api_key="test", llm_model="text", llm_vision_model="vision",
                        llm_thinking_mode="enabled", llm_reasoning_effort="medium",
                        llm_vision_reasoning_effort="medium")
    images = (_image_request().reference_image,) if vision else ()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAIChatAdapter(settings.llm(), client).complete(RoleRequest(system_prompt="test", payload={}, images=images))
    assert seen[0]["reasoning_effort"] == "medium"
    if vision:
        assert "thinking" not in seen[0]
    else:
        assert seen[0]["thinking"] == {"type": "enabled"}


@pytest.mark.parametrize("vision", [False, True])
async def test_legacy_requests_use_medium_effort_for_text_and_vision(vision):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(_review_payload()))

    role = replace(_role(vision_model="vision"), thinking_mode="enabled", reasoning_effort="medium", vision_reasoning_effort="medium")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAIChatAdapter(role, client).organize(_image_request() if vision else _request())
    assert seen[0]["reasoning_effort"] == "medium"


async def test_unspecified_effort_keeps_default_request_compatible():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion('{"ok":true}'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await OpenAIChatAdapter(_role(), client).complete(RoleRequest(system_prompt="test", payload={}))
    assert "reasoning_effort" not in seen[0] and "thinking" not in seen[0]


async def test_effort_changes_effective_model_fingerprint():
    async with httpx.AsyncClient() as client:
        adapter = OpenAIChatAdapter(_role(), client)
        medium = adapter._fingerprint("model", "https://llm.example/v1", "enabled", "medium")
        high = adapter._fingerprint("model", "https://llm.example/v1", "enabled", "high")
    assert medium != high
