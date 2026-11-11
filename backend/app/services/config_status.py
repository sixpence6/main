from __future__ import annotations

import hashlib
import re

from app.adapters.registry import AdapterRegistry
from app.config import LOCAL_REFERENCE_MAX_BYTES, RoleConfig, Settings
from app.schemas import (
    CapabilityStatus,
    ConfigIssue,
    ConfigStatus,
    GenerationGate,
    ImageGenerationConfigStatus,
    LLMConfigStatus,
    VisionConfigStatus,
    VisualCapabilities,
    VisualConfigStatus,
)

MISSING_FIELD_LABELS = {
    "adapter": "适配器",
    "base_url": "服务地址",
    "api_key": "API Key",
    "model": "模型",
    "vision_model": "视觉模型",
}

# 本地参考图规则；最终格式取本地规则与适配器声明的交集。
LOCAL_REFERENCE_MIME_TYPES = ("image/png", "image/jpeg")
CAPABILITY_LABELS = {"TEXT": "文字处理", "VISION": "视觉理解", "IMAGE_GEN": "图片生成"}
_SIZE_PAIR = re.compile(r"^(\d+)x(\d+)$")


def _fingerprint(adapter_id: str | None, model: str | None, base_url: str | None) -> str | None:
    """只暴露脱敏指纹；摘要不包含 Key，原始地址也不回传。"""
    if not adapter_id or not model:
        return None
    digest = hashlib.sha256(f"{adapter_id}|{model}|{base_url or ''}".encode()).hexdigest()[:32]
    return f"{adapter_id}:{model}:{digest}"


def _present(value: str | None) -> bool:
    return bool((value or "").strip())


def _source(explicit: str | None, inherited: str | None) -> str:
    if _present(explicit):
        return "independent"
    if _present(inherited):
        return "shared_text"
    return "missing"


def _is_valid_size(value: str | None) -> bool:
    match = _SIZE_PAIR.match(value or "")
    return bool(match) and int(match.group(1)) > 0 and int(match.group(2)) > 0


def _base_status(role: RoleConfig, adapter_known: bool, extra_issues: list[ConfigIssue]) -> dict:
    missing = role.missing_fields()
    issues = [
        ConfigIssue(code="CONFIG_MISSING_FIELD", message=f"缺少配置：{MISSING_FIELD_LABELS[name]}")
        for name in missing
    ]
    if role.configured and not adapter_known:
        issues.append(ConfigIssue(code="ADAPTER_UNAVAILABLE", message="适配器未注册，无法确认能力"))
    issues.extend(extra_issues)
    return {
        "configured": role.configured,
        "ready": role.configured and adapter_known and not extra_issues,
        "adapter_id": role.adapter_id,
        "model": role.model,
        "missing_fields": missing,
        "issues": issues,
        "timeout_seconds": role.timeout_seconds,
        "verification": "not_checked",
    }


def _visual_capabilities(
    visual_role: RoleConfig, caps, extra_issues: list[ConfigIssue]
) -> VisualCapabilities:
    usable_image_size = None
    effective_mime_types = None
    if caps is not None:
        if (
            _is_valid_size(visual_role.image_size)
            and caps.supported_image_sizes is not None
            and visual_role.image_size in caps.supported_image_sizes
        ):
            usable_image_size = visual_role.image_size
        if caps.reference_mime_types is not None:
            effective_mime_types = [
                mime for mime in LOCAL_REFERENCE_MIME_TYPES if mime in caps.reference_mime_types
            ]
    return VisualCapabilities(
        text_to_image=caps.text_to_image if caps is not None else None,
        reference_image=caps.reference_image if caps is not None else None,
        asynchronous=caps.asynchronous if caps is not None else None,
        supported_image_sizes=(
            list(caps.supported_image_sizes)
            if caps is not None and caps.supported_image_sizes is not None
            else None
        ),
        selected_image_size=visual_role.image_size,
        reference_mime_types=(
            list(caps.reference_mime_types)
            if caps is not None and caps.reference_mime_types is not None
            else None
        ),
        provider_max_reference_bytes=(
            caps.provider_max_reference_bytes if caps is not None else None
        ),
        effective_reference_max_bytes=(
            min(
                LOCAL_REFERENCE_MAX_BYTES,
                caps.provider_max_reference_bytes,
            )
            if caps is not None
            and caps.provider_max_reference_bytes is not None
            and caps.provider_max_reference_bytes > 0
            else LOCAL_REFERENCE_MAX_BYTES
        ),
        local_reference_max_bytes=LOCAL_REFERENCE_MAX_BYTES,
        effective_reference_mime_types=effective_mime_types,
        usable_image_size=usable_image_size,
    )


def _visual_status(
    visual_role: RoleConfig, adapter_known: bool, capabilities: VisualCapabilities, extra_issues: list[ConfigIssue]
) -> VisualConfigStatus:
    return VisualConfigStatus(
        **_base_status(visual_role, adapter_known, extra_issues),
        capabilities=capabilities,
    )


def _vision_status(settings: Settings, llm_role: RoleConfig, adapter_known: bool) -> VisionConfigStatus:
    """VISION 与 TEXT 分开建模，但允许显式共享或独立地址与凭据。"""
    adapter_id = llm_role.adapter_id
    vision_base_url = llm_role.vision_base_url or llm_role.base_url
    vision_api_key = llm_role.vision_api_key or llm_role.api_key
    vision_model = llm_role.vision_model

    missing: list[str] = []
    if not _present(adapter_id):
        missing.append("adapter")
    if not _present(vision_base_url):
        missing.append("base_url")
    if vision_api_key is None or not _present(vision_api_key.get_secret_value()):
        missing.append("api_key")
    if not _present(vision_model):
        missing.append("vision_model")
    configured = not missing

    issues = [
        ConfigIssue(code="CONFIG_MISSING_FIELD", message=f"缺少配置：{MISSING_FIELD_LABELS[name]}")
        for name in missing
    ]
    if configured and not adapter_known:
        issues.append(ConfigIssue(code="ADAPTER_UNAVAILABLE", message="适配器未注册，无法确认能力"))

    return VisionConfigStatus(
        capability="VISION",
        configured=configured,
        ready=configured and adapter_known,
        adapter_id=adapter_id,
        model=vision_model,
        missing_fields=missing,
        issues=issues,
        timeout_seconds=llm_role.timeout_seconds,
        verification="not_checked",
        model_fingerprint=_fingerprint(adapter_id, vision_model, vision_base_url),
        declared_capabilities=["text", "image_input"] if adapter_known else [],
        endpoint_source=_source(settings.llm_vision_base_url, llm_role.base_url),
        credential_source=_source(
            settings.llm_vision_api_key.get_secret_value() if settings.llm_vision_api_key else None,
            llm_role.api_key.get_secret_value() if llm_role.api_key else None,
        ),
    )


def _declared_image_capabilities(caps) -> list[str]:
    if caps is None:
        return []
    declared: list[str] = []
    if caps.text_to_image is True:
        declared.append("text_to_image")
    if caps.reference_image is True:
        declared.append("reference_image")
    if caps.asynchronous is True:
        declared.append("asynchronous")
    return declared


def build_config_status(settings: Settings, registry: AdapterRegistry) -> ConfigStatus:
    llm_role = settings.llm()
    llm_known = (llm_role.adapter_id or "") in registry.known_llm_ids()
    llm_status = LLMConfigStatus(**_base_status(llm_role, llm_known, []))

    visual_role = settings.visual()
    adapter_known = (
        bool(visual_role.adapter_id)
        and visual_role.adapter_id in registry.known_visual_ids()
    )
    extra_issues: list[ConfigIssue] = []
    caps = registry.visual_capabilities(visual_role.adapter_id or "") if adapter_known else None
    if caps is not None:
        if caps.text_to_image is not True:
            extra_issues.append(
                ConfigIssue(
                    code="TEXT_TO_IMAGE_UNAVAILABLE",
                    message="适配器未核实支持文生图",
                )
            )
        if caps.supported_image_sizes is None:
            extra_issues.append(
                ConfigIssue(
                    code="IMAGE_SIZE_UNVERIFIED",
                    message="适配器未核实支持的图片尺寸",
                )
            )
        elif not visual_role.image_size:
            extra_issues.append(
                ConfigIssue(
                    code="IMAGE_SIZE_NOT_CONFIGURED",
                    message="尚未配置生图尺寸",
                )
            )
        elif visual_role.image_size not in caps.supported_image_sizes:
            extra_issues.append(
                ConfigIssue(
                    code="IMAGE_SIZE_NOT_SUPPORTED",
                    message="当前尺寸不在适配器核实支持的尺寸内",
                )
            )
    elif visual_role.configured and adapter_known:
        extra_issues.append(
            ConfigIssue(
                code="CAPABILITIES_UNKNOWN",
                message="适配器能力未知",
            )
        )

    capabilities = _visual_capabilities(visual_role, caps, extra_issues)
    visual_status = _visual_status(visual_role, adapter_known, capabilities, extra_issues)

    text_status = CapabilityStatus(
        capability="TEXT",
        **llm_status.model_dump(),
        model_fingerprint=_fingerprint(llm_role.adapter_id, llm_role.model, llm_role.base_url),
        declared_capabilities=["text"] if llm_known else [],
    )
    vision_status = _vision_status(settings, llm_role, llm_known)
    image_data = visual_status.model_dump(exclude={"capabilities"})
    image_issues = list(visual_status.issues)
    image_ready = visual_status.ready and capabilities.usable_image_size is not None
    if visual_status.ready and capabilities.usable_image_size is None and visual_role.image_size:
        image_issues.append(
            ConfigIssue(
                code="IMAGE_SIZE_NOT_USABLE",
                message="当前尺寸不是合法像素尺寸，不能作为生图输出规格",
            )
        )
    image_data["ready"] = image_ready
    image_data["issues"] = image_issues
    image_status = ImageGenerationConfigStatus(
        capability="IMAGE_GEN",
        **image_data,
        model_fingerprint=_fingerprint(
            visual_role.adapter_id, visual_role.model, visual_role.base_url
        ),
        declared_capabilities=_declared_image_capabilities(caps),
        capabilities=visual_status.capabilities,
    )

    statuses = [
        ("TEXT", text_status),
        ("VISION", vision_status),
        ("IMAGE_GEN", image_status),
    ]
    missing_capabilities = [name for name, status in statuses if not status.ready]
    gate = GenerationGate(
        allowed=not missing_capabilities,
        missing_capabilities=missing_capabilities,
        issues=[
            ConfigIssue(
                code=f"{name}_NOT_READY",
                message=f"{CAPABILITY_LABELS[name]}能力未就绪，付费生成已阻止",
            )
            for name in missing_capabilities
        ],
        verification="not_checked",
    )
    return ConfigStatus(
        llm=llm_status,
        visual=visual_status,
        text=text_status,
        vision=vision_status,
        image_generation=image_status,
        generation=gate,
    )
