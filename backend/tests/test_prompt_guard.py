from __future__ import annotations

from app.prompt_guard import prompt_drift_errors


def _brief() -> dict:
    return {
        "product_description": "白色陶瓷马克杯",
        "purpose": "电商主图",
        "creative_direction": "浅灰纯色背景，柔和影棚光",
        "composition": "主体居中",
        "lighting": "柔和侧光",
        "size": "3840x2160",
        "style_keywords": ["极简"],
        "texts": [],
        "must_include": [],
        "must_preserve": [],
        "forbidden": ["价格标签", "促销文案"],
        "facts": [],
        "reference_usage": "none",
        "reference_asset_id": None,
    }


def test_negated_unconfirmed_terms_do_not_trigger_prompt_drift():
    payload = {
        "positive_prompt": (
            "电商主图：白色陶瓷马克杯，主体居中，极简风格，浅灰纯色背景，"
            "柔和影棚光和柔和侧光。仅展示已确认的商品本身，不添加未确认的"
            "配件、赠品、包装数量、文字或商标；不使用任何参考图。"
        ),
        "negative_prompt": "禁止出现价格标签、促销文案；不要添加额外价格信息。",
    }
    assert prompt_drift_errors(_brief(), payload) == []


def test_unnegated_gift_claim_still_requires_reconfirmation():
    payload = {
        "positive_prompt": "电商主图：白色陶瓷马克杯，购买即送赠品，极简风格。",
        "negative_prompt": "禁止出现价格标签、促销文案。",
    }
    errors = prompt_drift_errors(_brief(), payload)
    assert any("赠品" in error for error in errors)


def test_unnegated_price_and_marketing_claim_still_requires_reconfirmation():
    payload = {
        "positive_prompt": "电商主图：白色陶瓷马克杯，限时特价 9.9 元。",
        "negative_prompt": "禁止出现价格标签、促销文案。",
    }
    errors = prompt_drift_errors(_brief(), payload)
    assert any("限时" in error for error in errors)

def test_negation_scope_does_not_cross_a_comma():
    payload = {
        "positive_prompt": "电商主图：白色陶瓷马克杯，不要低价，赠送赠品。",
        "negative_prompt": "禁止出现价格标签、促销文案。",
    }
    errors = prompt_drift_errors(_brief(), payload)
    assert any("赠品" in error for error in errors)
