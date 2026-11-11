from __future__ import annotations

import asyncio
import base64
import binascii
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from app.adapters.base import (
    GenerationRequest,
    ImageCapabilities,
    ProviderError,
    ProviderOutcome,
)
from app.config import RoleConfig

_IMAGE_SIZES = ("3840x2160",)
_PROVIDER_SIZE_OVERRIDES = {"3840x2160": "16:9", "2160x3840": "9:16"}
_REFERENCE_MIME_TYPES = ("image/png", "image/jpeg")
_MAX_ERROR_MESSAGE_LENGTH = 200
_MAX_REDIRECTS = 5
_CDN_HOST_SUFFIXES = (".dimenoss.com",)
_URL_PATTERN = re.compile(r"https?://\S+")


def _safe_error_message(response: httpx.Response, secret: str) -> str:
    """Return a short provider message without headers, URLs, or credentials."""
    try:
        payload = response.json()
    except ValueError:
        return "生图服务拒绝了请求"
    if not isinstance(payload, dict):
        return "生图服务拒绝了请求"
    error = payload.get("error")
    message = error.get("message") if isinstance(error, dict) else payload.get("message")
    if not isinstance(message, str):
        return "生图服务拒绝了请求"
    cleaned = " ".join(message.split())
    if not cleaned:
        return "生图服务拒绝了请求"
    cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = _URL_PATTERN.sub("[url]", cleaned)
    return cleaned[:_MAX_ERROR_MESSAGE_LENGTH]


def _provider_size(image_size: str) -> str:
    """Translate a local pixel target into the provider's accepted size token."""
    return _PROVIDER_SIZE_OVERRIDES.get(image_size, image_size)


def _status_error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "VISUAL_AUTH_FAILED"
    if status_code == 429:
        return "VISUAL_RATE_LIMITED"
    return "VISUAL_REQUEST_REJECTED"


def _reference_mime_type(request: GenerationRequest, path: Path) -> str:
    if request.reference_mime_type in _REFERENCE_MIME_TYPES:
        return request.reference_mime_type
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "image/png"


def _reference_filename(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    return f"reference{suffix}"


class OpenAIImagesAdapter:
    """OpenAI-compatible image endpoint with synchronous base64 results.

    The adapter asks for base64 explicitly. Some compatible gateways otherwise
    return a short-lived CDN URL; that fallback is supported through
    fetch_result with a provider-host allowlist and no model credentials.
    """

    adapter_id = "openai-images"

    def __init__(self, role: RoleConfig, client: httpx.AsyncClient) -> None:
        if not role.base_url or not role.api_key or not role.model:
            raise ValueError("openai-images 适配器缺少必要配置")
        self.model = role.model
        self.capabilities = ImageCapabilities(
            text_to_image=True,
            reference_image=True,
            asynchronous=False,
            supported_image_sizes=_IMAGE_SIZES,
            reference_mime_types=_REFERENCE_MIME_TYPES,
            provider_max_reference_bytes=None,
            poll_interval_seconds=None,
            normalize_result_size=True,
        )
        self._base_url = role.base_url.rstrip("/")
        self._api_key = role.api_key.get_secret_value()
        self._timeout_seconds = role.timeout_seconds
        self._default_image_size = role.image_size or _IMAGE_SIZES[0]
        self._client = client
        self._allowed_fetch_hosts = self._build_allowed_fetch_hosts()

    def _build_allowed_fetch_hosts(self) -> frozenset[str]:
        base_host = (urlsplit(self._base_url).hostname or "").lower()
        return frozenset({base_host}) if base_host else frozenset()

    async def submit(self, request: GenerationRequest) -> ProviderOutcome:
        image_size = _provider_size(request.image_size or self._default_image_size)
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            if request.reference_path is None:
                response = await self._client.post(
                    f"{self._base_url}/images/generations",
                    headers=headers,
                    json={
                        "model": self.model,
                        "prompt": request.prompt,
                        "size": image_size,
                        "n": request.count,
                        "response_format": request.response_format,
                    },
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                )
            else:
                reference_bytes = request.reference_bytes
                if reference_bytes is None:
                    reference_bytes = await asyncio.to_thread(request.reference_path.read_bytes)
                response = await self._client.post(
                    f"{self._base_url}/images/edits",
                    headers=headers,
                    data={
                        "model": self.model,
                        "prompt": request.prompt,
                        "size": image_size,
                        "n": str(request.count),
                        "response_format": request.response_format,
                    },
                    files={
                        "image": (
                            _reference_filename(request.reference_path),
                            reference_bytes,
                            _reference_mime_type(request, request.reference_path),
                        )
                    },
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                )
        except (FileNotFoundError, OSError):
            raise ProviderError(
                "参考图片无法读取",
                evidence="not_submitted",
                error_code="REFERENCE_FILE_MISSING",
            ) from None
        except httpx.TimeoutException:
            raise ProviderError(
                "生图请求超时，结果未确认",
                evidence="uncertain",
                error_code="VISUAL_TIMEOUT",
            ) from None
        except httpx.RequestError:
            raise ProviderError(
                "生图请求未能确认送达",
                evidence="uncertain",
                error_code="VISUAL_NETWORK_ERROR",
            ) from None

        return self._interpret_response(response)

    def _interpret_response(self, response: httpx.Response) -> ProviderOutcome:
        if 300 <= response.status_code < 400:
            raise ProviderError(
                "生图服务返回了不允许的重定向",
                evidence="rejected",
                error_code="VISUAL_REQUEST_REJECTED",
            )
        if response.status_code >= 500:
            raise ProviderError(
                "生图服务暂时不可用",
                evidence="uncertain",
                error_code="VISUAL_UPSTREAM_ERROR",
            )
        if response.status_code >= 400:
            raise ProviderError(
                _safe_error_message(response, self._api_key),
                evidence="rejected",
                error_code=_status_error_code(response.status_code),
            )
        if not 200 <= response.status_code < 300:
            raise ProviderError(
                "生图服务返回了未预期的状态",
                evidence="uncertain",
                error_code="VISUAL_RESPONSE_INVALID",
            )

        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(
                "生图接口返回格式异常",
                evidence="uncertain",
                error_code="VISUAL_RESPONSE_INVALID",
            ) from None
        if not isinstance(payload, dict):
            raise ProviderError(
                "生图接口返回格式异常",
                evidence="uncertain",
                error_code="VISUAL_RESPONSE_INVALID",
            )
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise ProviderError(
                "生图接口未返回图片",
                evidence="rejected",
                error_code="VISUAL_NO_IMAGE",
            )
        if len(data) != 1:
            raise ProviderError(
                "生图接口返回了多张图片",
                evidence="result_available",
                error_code="VISUAL_MULTIPLE_IMAGES",
            )
        item = data[0]
        if not isinstance(item, dict):
            raise ProviderError(
                "生图接口返回格式异常",
                evidence="uncertain",
                error_code="VISUAL_RESPONSE_INVALID",
            )

        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded:
            if encoded.startswith("data:"):
                _, _, encoded = encoded.partition(",")
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ProviderError(
                    "生图结果图片无法解码",
                    evidence="result_available",
                    error_code="RESULT_FETCH_FAILED",
                ) from None
            return ProviderOutcome(
                state="succeeded",
                image_bytes=image_bytes,
                image_count=1,
                evidence="result_available",
            )

        locator = item.get("url")
        if isinstance(locator, str) and locator:
            return ProviderOutcome(
                state="succeeded",
                result_locator=locator,
                image_count=1,
                evidence="result_available",
            )

        raise ProviderError(
            "生图接口未返回可用的图片",
            evidence="rejected",
            error_code="VISUAL_NO_IMAGE",
        )

    async def poll(self, remote_task_id: str) -> ProviderOutcome:
        raise ProviderError(
            "该适配器为同步接口，不支持轮询",
            evidence="uncertain",
            error_code="VISUAL_POLL_UNSUPPORTED",
            remote_task_id=remote_task_id,
        )

    def _approved_fetch_url(self, locator: str) -> str:
        parts = urlsplit(locator)
        if parts.scheme != "https" or not parts.hostname:
            raise ProviderError(
                "生图结果地址无效",
                evidence="result_available",
                error_code="RESULT_FETCH_FAILED",
            )
        host = parts.hostname.lower()
        if host not in self._allowed_fetch_hosts and not any(
            host.endswith(suffix) for suffix in _CDN_HOST_SUFFIXES
        ):
            raise ProviderError(
                "生图结果地址不在允许的下载域名内",
                evidence="result_available",
                error_code="RESULT_FETCH_FAILED",
            )
        return locator

    async def fetch_result(self, locator: str) -> ProviderOutcome:
        current = self._approved_fetch_url(locator)
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                response = await self._client.get(
                    current,
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                )
            except httpx.TimeoutException:
                raise ProviderError(
                    "生图结果下载超时",
                    evidence="result_available",
                    error_code="RESULT_FETCH_FAILED",
                ) from None
            except httpx.RequestError:
                raise ProviderError(
                    "生图结果下载失败",
                    evidence="result_available",
                    error_code="RESULT_FETCH_FAILED",
                ) from None

            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    raise ProviderError(
                        "生图结果下载重定向无效",
                        evidence="result_available",
                        error_code="RESULT_FETCH_FAILED",
                    )
                current = self._approved_fetch_url(urljoin(current, location))
                continue

            if response.status_code != 200:
                raise ProviderError(
                    "生图结果下载失败",
                    evidence="result_available",
                    error_code="RESULT_FETCH_FAILED",
                )
            if not response.content:
                raise ProviderError(
                    "生图结果为空",
                    evidence="result_available",
                    error_code="RESULT_FETCH_FAILED",
                )
            return ProviderOutcome(
                state="succeeded",
                image_bytes=response.content,
                image_count=1,
                evidence="result_available",
            )

        raise ProviderError(
            "生图结果下载重定向过多",
            evidence="result_available",
            error_code="RESULT_FETCH_FAILED",
        )


OPENAI_IMAGES_CAPABILITIES = ImageCapabilities(
    text_to_image=True,
    reference_image=True,
    asynchronous=False,
    supported_image_sizes=_IMAGE_SIZES,
    normalize_result_size=True,
    reference_mime_types=_REFERENCE_MIME_TYPES,
    provider_max_reference_bytes=None,
    poll_interval_seconds=None,
)
