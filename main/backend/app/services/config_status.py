from __future__ import annotations

from app.adapters.registry import AdapterRegistry
from app.config import LOCAL_REFERENCE_MAX_BYTES, RoleConfig, Settings
from app.schemas import (
    ConfigIssue,
    ConfigStatus,
    LLMConfigStatus,
    VisualCapabilities,
    VisualConfigStatus,
)

MISSING_FIELD_LABELS = {
    "adapter": "适配器",
    "base_url": "服务地址",
    "api_key": "API Key",
    "model": "模型",
}


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


def build_config_status(settings: Settings, registry: AdapterRegistry) -> ConfigStatus:
    llm_role = settings.llm()
    llm_status = LLMConfigStatus(**_base_status(llm_role, llm_role.adapter_id in registry.known_llm_ids(), []))

    visual_role = settings.visual()
    adapter_known = bool(visual_role.adapter_id) and visual_role.adapter_id in registry.known_visual_ids()
    extra_issues: list[ConfigIssue] = []
    capabilities = VisualCapabilities(
        text_to_image=None,
        reference_image=None,
        asynchronous=None,
        supported_image_sizes=None,
        selected_image_size=visual_role.image_size,
        reference_mime_types=None,
        provider_max_reference_bytes=None,
        effective_reference_max_bytes=LOCAL_REFERENCE_MAX_BYTES,
    )
    caps = registry.visual_capabilities(visual_role.adapter_id or "") if adapter_known else None
    if caps is not None:
        effective = LOCAL_REFERENCE_MAX_BYTES
        if caps.provider_max_reference_bytes is not None:
            effective = min(effective, caps.provider_max_reference_bytes)
        capabilities = VisualCapabilities(
            text_to_image=caps.text_to_image,
            reference_image=caps.reference_image,
            asynchronous=caps.asynchronous,
            supported_image_sizes=(
                list(caps.supported_image_sizes) if caps.supported_image_sizes is not None else None
            ),
            selected_image_size=visual_role.image_size,
            reference_mime_types=(
                list(caps.reference_mime_types) if caps.reference_mime_types is not None else None
            ),
            provider_max_reference_bytes=caps.provider_max_reference_bytes,
            effective_reference_max_bytes=effective,
        )
        if (
            visual_role.image_size
            and caps.supported_image_sizes is not None
            and visual_role.image_size not in caps.supported_image_sizes
        ):
            extra_issues.append(
                ConfigIssue(code="IMAGE_SIZE_NOT_SUPPORTED", message="当前尺寸不在适配器核实支持的尺寸内")
            )

    visual_status = VisualConfigStatus(
        **_base_status(visual_role, adapter_known, extra_issues),
        capabilities=capabilities,
    )
    return ConfigStatus(llm=llm_status, visual=visual_status)
