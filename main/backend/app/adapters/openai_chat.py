from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any

import httpx

from app.adapters.base import (
    BriefData,
    OrganizeImage,
    OrganizeRequest,
    OrganizeResult,
    ProviderError,
    RoleRequest,
    RoleResponse,
)
from app.config import RoleConfig

_MAX_ERROR_MESSAGE_LENGTH = 200
# Reasoning models spend part of this budget on hidden reasoning tokens before
# emitting the required JSON answer; a small completion budget can therefore
# finish with an empty content string. Keep enough room for both.
_MAX_OUTPUT_TOKENS = 32768
_MAX_IMAGE_BYTES = 10_000_000
_IMAGE_MIME_TYPES = {"image/png", "image/jpeg"}
_URL_PATTERN = re.compile(r"https?://\S+")
_FIELD_NAMES = (
    "product_description",
    "purpose",
    "style",
    "composition",
    "text_content",
)

_SYSTEM_PROMPT = """你是电商生图工作台的需求整理助手。
你的任务是把用户的产品描述和对话整理成可编辑的需求卡，
并在信息足够时生成完整的生图提示词。

只输出一个 JSON 对象，不要输出 Markdown 代码块、前后缀说明或任何额外文字。

需要继续追问时，输出：
{"status":"needs_input","content":"给用户看的简短中文问题","fields":{"product_description":"已经确定的商品描述"}}

信息足够时，输出：
{"status":"review","content":"给用户看的简短中文确认语","fields":{"product_description":"商品描述","purpose":"图片用途","style":"画面风格","composition":"构图和场景","text_content":"要出现在图片上的文字，没有则为空字符串"},"prompt":"完整生图提示词"}

规则：
- needs_input 的 fields 可以只包含已经确定的字段；不确定的字段省略或用空字符串。
- review 的 fields 必须包含上面五个字段，且每个值都是字符串。
- review 的 prompt 必须非空，并覆盖商品主体、用途、风格、构图和文字要求。
- 商品主体、价格或活动信息有歧义时集中提问；不要一次只问一个零碎问题。
- 用户已经给出的约束必须保留，不要擅自改变商品、价格、活动或文字内容。
- 未知材质、功能、认证和价格保持未指定；风格、构图可以给出可见建议。
- 尺寸、参考图、模型和资源 ID 由服务端管理，不要输出这些字段，也不要让用户选择。
- 如果本轮附带了参考图，先观察商品外观、颜色、材质、结构、包装和可见文字，
  并把这些作为商品描述的依据；看不清或不确定的细节不要编造。
- 用户文字与参考图冲突时，以用户明确的文字为准，并在 content 中说明。
- content 和字段使用用户当前使用的语言。"""


def _safe_error_message(response: httpx.Response, secret: str) -> str:
    """Return a short provider message without credentials or signed URLs."""
    try:
        payload = response.json()
    except ValueError:
        return "语言模型服务拒绝了请求"
    if not isinstance(payload, dict):
        return "语言模型服务拒绝了请求"
    error = payload.get("error")
    message = error.get("message") if isinstance(error, dict) else payload.get("message")
    if not isinstance(message, str):
        return "语言模型服务拒绝了请求"
    cleaned = " ".join(message.split())
    if not cleaned:
        return "语言模型服务拒绝了请求"
    cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = _URL_PATTERN.sub("[url]", cleaned)
    return cleaned[:_MAX_ERROR_MESSAGE_LENGTH]


def _status_error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "LLM_AUTH_FAILED"
    if status_code == 429:
        return "LLM_RATE_LIMITED"
    return "LLM_REQUEST_REJECTED"


def _content_text(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts) if parts else None
    return None


def _only_outer_brace_missing(candidate: str) -> bool:
    """Return True when the candidate is complete except for one closing brace."""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return False
            opener = stack.pop()
            if (opener, char) not in {("{", "}"), ("[", "]")}:
                return False
    return not in_string and stack == ["{"]


def _extract_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        start = cleaned.find("{")
        if start < 0:
            raise
        end = cleaned.rfind("}")
        if end > start:
            candidate = cleaned[start : end + 1]
            try:
                return json.loads(candidate)
            except ValueError:
                pass
        tail = cleaned[start:].strip()
        if _only_outer_brace_missing(tail):
            return json.loads(tail + "}")
        raise


def _field_value(fields: dict[str, Any], name: str) -> str:
    value = fields.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError(
            f"整理结果字段 {name} 不是字符串",
            evidence="rejected",
            error_code="LLM_RESPONSE_INVALID",
        )
    return value


class OpenAIChatAdapter:
    """OpenAI-compatible chat completion adapter for the organize operation."""

    adapter_id = "openai-chat"

    def __init__(self, role: RoleConfig, client: httpx.AsyncClient) -> None:
        if not role.base_url or not role.api_key or not role.model:
            raise ValueError("openai-chat 适配器缺少必要配置")
        self.model = role.model
        self._vision_model = role.vision_model
        self._base_url = role.base_url.rstrip("/")
        self._vision_base_url = (role.vision_base_url or role.base_url).rstrip("/")
        self._api_key = role.api_key.get_secret_value()
        self._vision_api_key = (
            role.vision_api_key.get_secret_value()
            if role.vision_api_key
            else self._api_key
        )
        self._timeout_seconds = role.timeout_seconds
        self._client = client

    def _messages(self, request: OrganizeRequest) -> list[dict[str, Any]]:
        context = {
            "current_brief": request.brief.as_dict(),
            "has_reference_image": request.has_reference_image,
            "reference_image_attached": request.reference_image is not None,
        }
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "当前上下文（只读）：\n"
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        for message in request.messages:
            if not message.content:
                continue
            role = message.role if message.role in {"user", "assistant", "system"} else "user"
            messages.append({"role": role, "content": message.content})
        if not request.messages or request.messages[-1].content != request.user_text:
            messages.append({"role": "user", "content": request.user_text})
        if request.reference_image is not None:
            self._attach_reference_image(messages, request.reference_image)
        return messages

    def _attach_reference_image(
        self, messages: list[dict[str, Any]], image: OrganizeImage
    ) -> None:
        if image.mime_type not in _IMAGE_MIME_TYPES:
            raise ProviderError(
                "参考图格式不支持",
                evidence="rejected",
                error_code="LLM_IMAGE_UNSUPPORTED",
            )
        if not image.data or len(image.data) > _MAX_IMAGE_BYTES:
            raise ProviderError(
                "参考图大小超出多模态模型限制",
                evidence="rejected",
                error_code="LLM_IMAGE_TOO_LARGE",
            )
        encoded = base64.b64encode(image.data).decode("ascii")
        image_part = {
            "type": "image_url",
            "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
        }
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = [
                    {"type": "text", "text": content},
                    image_part,
                ]
            elif isinstance(content, list):
                content.append(image_part)
            else:
                message["content"] = [image_part]
            return
        messages.append({"role": "user", "content": [image_part]})


    def _fingerprint(self, model: str, base_url: str) -> str:
        """A stable, non-secret identifier for the model configuration in use."""
        digest = hashlib.sha256(
            f"{self.adapter_id}|{model}|{base_url}".encode()
        ).hexdigest()[:32]
        return f"{self.adapter_id}:{model}:{digest}"

    async def complete(self, request: RoleRequest) -> RoleResponse:
        """Execute one strict role call and return its single JSON object.

        The adapter performs exactly one HTTP attempt. Contract validation and
        business acceptance intentionally live in the workflow layer.
        """
        model = self.model
        base_url = self._base_url
        api_key = self._api_key
        if request.images:
            if not self._vision_model:
                raise ProviderError(
                    "未配置多模态模型，无法处理图片",
                    evidence="rejected",
                    error_code="LLM_VISION_NOT_CONFIGURED",
                )
            model = self._vision_model
            base_url = self._vision_base_url
            api_key = self._vision_api_key

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": request.system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    request.payload, ensure_ascii=False, separators=(",", ":")
                ),
            },
        ]
        for image in request.images:
            self._attach_reference_image(messages, image)

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }
        # Some OpenAI-compatible vision backends reject structured output
        # (for example, "does not implement structured text.format output").
        # The role prompts still require one JSON object, and the parser
        # accepts fenced or otherwise slightly wrapped JSON.
        if not request.images:
            body["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ProviderError(
                "语言模型请求超时",
                evidence="uncertain",
                error_code="LLM_TIMEOUT",
            ) from None
        except httpx.RequestError:
            raise ProviderError(
                "语言模型请求未能确认送达",
                evidence="uncertain",
                error_code="LLM_NETWORK_ERROR",
            ) from None

        payload = self._interpret_role_response(response, api_key)
        return RoleResponse(
            payload=payload,
            model_fingerprint=self._fingerprint(model, base_url),
        )

    def _interpret_role_response(
        self, response: httpx.Response, api_key: str
    ) -> dict[str, object]:
        if 300 <= response.status_code < 400:
            raise ProviderError(
                "语言模型服务返回了不允许的重定向",
                evidence="rejected",
                error_code="LLM_REQUEST_REJECTED",
            )
        if response.status_code >= 500:
            raise ProviderError(
                "语言模型服务暂时不可用",
                evidence="uncertain",
                error_code="LLM_UPSTREAM_ERROR",
            )
        if response.status_code >= 400:
            raise ProviderError(
                _safe_error_message(response, api_key),
                evidence="rejected",
                error_code=_status_error_code(response.status_code),
            )
        if not 200 <= response.status_code < 300:
            raise ProviderError(
                "语言模型服务返回了未预期的状态",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        try:
            body = response.json()
        except ValueError:
            raise ProviderError(
                "语言模型返回格式异常",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            ) from None
        if not isinstance(body, dict):
            raise ProviderError(
                "语言模型返回格式异常",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                "语言模型没有返回候选结果",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        content = _content_text(message)
        if not content or not content.strip():
            raise ProviderError(
                "语言模型没有返回内容",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        try:
            parsed = _extract_json(content)
        except (ValueError, TypeError):
            raise ProviderError(
                "语言模型返回的内容不是有效 JSON",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            ) from None
        if not isinstance(parsed, dict):
            raise ProviderError(
                "语言模型返回的内容不是 JSON 对象",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        return parsed

    async def organize(self, request: OrganizeRequest) -> OrganizeResult:
        model = self.model
        base_url = self._base_url
        api_key = self._api_key
        if request.reference_image is not None:
            if not self._vision_model:
                raise ProviderError(
                    "未配置多模态模型，无法理解参考图",
                    evidence="rejected",
                    error_code="LLM_VISION_NOT_CONFIGURED",
                )
            model = self._vision_model
            base_url = self._vision_base_url
            api_key = self._vision_api_key
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages(request),
            "temperature": 0.2,
            "max_tokens": _MAX_OUTPUT_TOKENS,
        }
        # Vision backends may not implement structured output; keep the
        # explicit JSON requirement in the organizer prompt instead.
        if request.reference_image is None:
            body["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ProviderError(
                "语言模型请求超时",
                evidence="uncertain",
                error_code="LLM_TIMEOUT",
            ) from None
        except httpx.RequestError:
            raise ProviderError(
                "语言模型请求未能确认送达",
                evidence="uncertain",
                error_code="LLM_NETWORK_ERROR",
            ) from None

        return self._interpret_response(
            response, request.brief.image_size, api_key
        )

    def _interpret_response(
        self, response: httpx.Response, image_size: str | None, api_key: str
    ) -> OrganizeResult:
        if 300 <= response.status_code < 400:
            raise ProviderError(
                "语言模型服务返回了不允许的重定向",
                evidence="rejected",
                error_code="LLM_REQUEST_REJECTED",
            )
        if response.status_code >= 500:
            raise ProviderError(
                "语言模型服务暂时不可用",
                evidence="uncertain",
                error_code="LLM_UPSTREAM_ERROR",
            )
        if response.status_code >= 400:
            raise ProviderError(
                _safe_error_message(response, api_key),
                evidence="rejected",
                error_code=_status_error_code(response.status_code),
            )
        if not 200 <= response.status_code < 300:
            raise ProviderError(
                "语言模型服务返回了未预期的状态",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )

        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(
                "语言模型返回格式异常",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            ) from None
        if not isinstance(payload, dict):
            raise ProviderError(
                "语言模型返回格式异常",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                "语言模型没有返回候选结果",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        content = _content_text(message)
        if not content or not content.strip():
            raise ProviderError(
                "语言模型没有返回整理内容",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )
        try:
            result = _extract_json(content)
        except (ValueError, TypeError):
            raise ProviderError(
                "语言模型返回的整理结果不是有效 JSON",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            ) from None
        if not isinstance(result, dict):
            raise ProviderError(
                "语言模型返回的整理结果不是对象",
                evidence="uncertain",
                error_code="LLM_RESPONSE_INVALID",
            )

        status = result.get("status")
        if status not in {"needs_input", "review"}:
            raise ProviderError(
                "整理结果状态无效",
                evidence="rejected",
                error_code="LLM_RESPONSE_INVALID",
            )
        assistant_message = result.get("content")
        if not isinstance(assistant_message, str) or not assistant_message.strip():
            raise ProviderError(
                "整理结果缺少助手消息",
                evidence="rejected",
                error_code="LLM_RESPONSE_INVALID",
            )
        fields = result.get("fields")
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            raise ProviderError(
                "整理结果需求卡无效",
                evidence="rejected",
                error_code="LLM_RESPONSE_INVALID",
            )
        if status == "review":
            missing_fields = [name for name in _FIELD_NAMES if name not in fields]
            if missing_fields:
                raise ProviderError(
                    "整理结果需求卡缺少字段",
                    evidence="rejected",
                    error_code="LLM_RESPONSE_INVALID",
                )
        brief_values = {name: _field_value(fields, name) for name in _FIELD_NAMES}
        prompt = result.get("prompt", "")
        if prompt is None:
            prompt = ""
        if not isinstance(prompt, str):
            raise ProviderError(
                "整理结果提示词无效",
                evidence="rejected",
                error_code="LLM_RESPONSE_INVALID",
            )
        if status == "review" and not prompt.strip():
            raise ProviderError(
                "整理结果缺少完整提示词",
                evidence="rejected",
                error_code="LLM_RESPONSE_INVALID",
            )

        return OrganizeResult(
            outcome=status,
            assistant_message=assistant_message.strip(),
            brief=BriefData(
                **brief_values,
                image_size=image_size,
                reference_asset_id=None,
                prompt=prompt if status == "review" else "",
            ),
        )
