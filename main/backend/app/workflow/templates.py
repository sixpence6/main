from __future__ import annotations

from string import Formatter
from typing import Final

TEMPLATE_VERSION: Final = "2.0.0"

STAGE_LABELS: Final[dict[str, str]] = {
    "S01": "判断请求范围",
    "S02": "分析参考图",
    "S03": "整理需求",
    "S04": "策划创意",
    "S05": "制定视觉方案",
    "S06": "审查需求卡",
    "S07": "编制生图提示词",
    "S08": "生成图片",
    "S09": "检查图片",
    "S10": "审查输出",
    "S11": "准备交付",
}

CAPABILITY_LABELS: Final[dict[str, str]] = {
    "TEXT": "文字处理",
    "VISION": "视觉理解",
    "IMAGE_GEN": "图片生成",
}

TEMPLATES: Final[dict[str, str]] = {
    "SCOPE_REFUSAL": "抱歉无法回答此内容",
    "INTENT_UNCLEAR": "请说明你要制作的商品图片及用途。",
    "ACTION_UNAVAILABLE": "当前状态不支持此操作，请先完成提示的步骤。",
    "WAIT_STAGE": "正在{stage_label}，请稍候。",
    "REFERENCE_INVALID": "文件无法作为参考图使用，请上传符合要求的 PNG 或 JPEG 图片。",
    "CAPABILITY_NOT_READY": "当前{capability_label}能力未配置或不满足要求，流程已暂停。",
    "SERVICE_UNAVAILABLE": "当前处理服务暂不可用，已保留输入，本次未自动重试。",
    "REVIEW_UNAVAILABLE": "审查暂不可用，内容尚未放行。",
    "CONFIRM_REQUIRED": "请先确认当前需求卡，再明确提交生成。",
    "IMAGE_NOT_APPROVED": "本次图片未通过检查，未展示或提供下载。你可以补充需求后重新确认并提交生成。",
    "RUN_SUPERSEDED": "需求已更新，旧处理结果不会覆盖当前版本。",
    "GENERATION_UNKNOWN": "上次生成结果尚未确认，可能已经计费。再次生成可能产生额外费用。",
    "PROCESS_INTERRUPTED": "流程因服务中断暂停，未自动继续执行。",
    "LEGACY_UNREVIEWED": "此历史内容尚未通过 v2 审查，暂不可查看或下载。可以主动发起补审。",
    "ASSET_UNAVAILABLE": "当前内容不可访问或批准已失效，请检查状态或重新审查。",
    "SAFETY_REFUSAL": "抱歉，无法处理该图片请求。",
}

_PARAMS: Final[dict[str, tuple[str, ...]]] = {
    "WAIT_STAGE": ("stage_label",),
    "CAPABILITY_NOT_READY": ("capability_label",),
}

SAFE_FALLBACK: Final = TEMPLATES["SERVICE_UNAVAILABLE"]


def render(template_id: str, /, **params: object) -> str:
    text = TEMPLATES.get(template_id)
    if text is None:
        return SAFE_FALLBACK
    allowed = _PARAMS.get(template_id, ())
    if set(params) != set(allowed):
        return SAFE_FALLBACK
    if template_id == "WAIT_STAGE":
        label = params.get("stage_label")
        if not isinstance(label, str) or label not in STAGE_LABELS.values():
            return SAFE_FALLBACK
    if template_id == "CAPABILITY_NOT_READY":
        label = params.get("capability_label")
        if not isinstance(label, str) or label not in CAPABILITY_LABELS.values():
            return SAFE_FALLBACK
    try:
        names = {name for _, name, _, _ in Formatter().parse(text) if name}
        if set(names) != set(allowed):
            return SAFE_FALLBACK
        return text.format(**params)
    except (KeyError, ValueError, IndexError):
        return SAFE_FALLBACK


def stage_label(stage: str) -> str | None:
    return STAGE_LABELS.get(stage)


def capability_label(capability: str) -> str | None:
    return CAPABILITY_LABELS.get(capability)
