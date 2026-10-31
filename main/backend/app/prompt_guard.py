from __future__ import annotations

import re
from typing import Any

# Prompt compilation may phrase the confirmed card, but it may not introduce a
# new selling point, image text, claim or restriction that the user has not
# confirmed. These tokens are deliberately conservative and deterministic;
# semantic review remains the independent output reviewer's job.
_RISK_TOKENS = (
    "限时",
    "特价",
    "折扣",
    "优惠",
    "秒杀",
    "包邮",
    "赠品",
    "赠送",
    "加赠",
    "买一送一",
    "立减",
    "到手价",
    "促销",
    "清仓",
    "甩卖",
    "限量",
    "独家",
    "首发",
    "新品",
    "全新",
    "升级",
    "爆款",
    "热销",
    "网红",
    "同款",
    "正品",
    "官方",
    "认证",
    "专利",
    "第一",
    "顶级",
    "绝对",
    "永久",
    "100%",
    "纯天然",
    "零添加",
)
_NUMERIC_PRICE = re.compile(r"\d+(?:\.\d+)?\s*(?:元|折|%|％)")
_QUOTED = re.compile(r"[“\"']([^”\"']{2,40})[”\"']")
_UPPER_TEXT = re.compile(r"\b[A-Z][A-Z0-9 &-]{2,}\b")
_SPLIT = re.compile(r"[；;\n。]+")
_TRIM = re.compile(r"[\s，,。；;：:、\-—()（）\[\]【】“”\"'!?！？/]")
_SAFE_PHRASES = tuple(
    sorted(
        (
            "画面",
            "图片",
            "图像",
            "整体",
            "保持",
            "呈现",
            "展示",
            "体现",
            "采用",
            "使用",
            "生成",
            "制作",
            "电商",
            "主图",
            "摄影",
            "照片",
            "渲染",
            "插画",
            "场景",
            "氛围",
            "风格",
            "色彩",
            "色调",
            "细节",
            "质感",
            "清晰",
            "高清",
            "真实",
            "自然",
            "简洁",
            "干净",
            "舒适",
            "美观",
            "统一",
            "协调",
            "留白",
            "空间",
            "视觉",
            "效果",
            "主体",
            "产品",
            "商品",
            "背景",
            "光影",
            "光线",
            "构图",
            "参考图",
            "仅用于",
            "不复制",
            "不要",
            "不得",
            "禁止",
            "避免",
            "添加",
            "出现",
            "包含",
            "任何",
            "文字",
            "水印",
            "标志",
            "logo",
            "单张",
            "一张",
            "以",
            "并",
            "且",
            "和",
            "与",
            "的",
            "地",
            "得",
            "为",
            "在",
            "中",
            "上",
            "下",
            "前",
            "后",
            "左",
            "右",
            "中心",
            "内",
            "外",
            "RGB",
            "CMYK",
            "PNG",
            "JPEG",
            "AI",
            "3D",
            "4K",
            "8K",
            "HDR",
        ),
        key=len,
        reverse=True,
    )
)


def _fragments(brief: dict[str, Any]) -> list[str]:
    values: list[Any] = [
        brief.get("product_description"),
        brief.get("purpose"),
        brief.get("creative_direction"),
        brief.get("composition"),
        brief.get("lighting"),
        brief.get("size"),
        brief.get("reference_usage"),
    ]
    values.extend(brief.get("style_keywords") or [])
    values.extend(brief.get("texts") or [])
    values.extend(brief.get("must_include") or [])
    values.extend(brief.get("must_preserve") or [])
    values.extend(brief.get("forbidden") or [])
    for fact in brief.get("facts") or []:
        if isinstance(fact, dict):
            values.append(fact.get("text"))
    fragments: list[str] = []
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if len(text) >= 2:
                fragments.append(text)
    if brief.get("reference_asset_id"):
        fragments.append("参考图仅用于商品外观，不复制背景")
    return sorted(set(fragments), key=len, reverse=True)


def _residual(clause: str, fragments: list[str]) -> str:
    text = clause
    for fragment in fragments:
        text = text.replace(fragment, "")
    for phrase in _SAFE_PHRASES:
        text = text.replace(phrase, "")
    return _TRIM.sub("", text)


def _grounded(text: str, fragments: list[str]) -> bool:
    if not text.strip():
        return True
    if any(text.strip() == fragment for fragment in fragments):
        return True
    return not _residual(text, fragments)


def _quoted_text(text: str) -> list[str]:
    return [item.strip() for item in _QUOTED.findall(text) if item.strip()]


def _has_unconfirmed_risk(text: str, grounded_text: str) -> list[str]:
    found: list[str] = []
    for token in _RISK_TOKENS:
        if token in text and token not in grounded_text:
            found.append(token)
    for token in _NUMERIC_PRICE.findall(text):
        if token not in grounded_text:
            found.append(token)
    for token in _UPPER_TEXT.findall(text):
        token = token.strip()
        if token and token not in grounded_text and token not in _SAFE_PHRASES:
            found.append(token)
    for token in _quoted_text(text):
        if token not in grounded_text:
            found.append(token)
    return found


def prompt_drift_errors(brief: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """Return deterministic evidence that a prompt left the confirmed card.

    The caller must not create a prompt artifact or approval when this returns
    anything. The user has to revise the requirements and confirm again.
    """
    fragments = _fragments(brief)
    grounded_text = "；".join(fragments)
    errors: list[str] = []
    positive = payload.get("positive_prompt") or ""
    negative = payload.get("negative_prompt") or ""
    if not isinstance(positive, str) or not isinstance(negative, str):
        return ["提示词字段类型无效"]
    for clause in _SPLIT.split(positive):
        if clause.strip() and not _grounded(clause, fragments):
            errors.append("positive_prompt 含未确认内容")
    for clause in _SPLIT.split(negative):
        if clause.strip() and not _grounded(clause, fragments):
            errors.append("negative_prompt 含未确认内容")
    for token in _has_unconfirmed_risk(positive, grounded_text):
        errors.append(f"提示词含未确认卖点或文字：{token}")
    for token in _has_unconfirmed_risk(negative, grounded_text):
        errors.append(f"提示词含未确认限制：{token}")
    # De-duplicate while preserving the first deterministic reason.
    return list(dict.fromkeys(errors))


__all__ = ["prompt_drift_errors"]
