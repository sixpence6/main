"""Program-owned requirement accumulation and structured clarification bindings."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.db import write_transaction
from app.errors import ApiError
from app.workflow.contracts import BriefData, ClarificationQuestion
from app.workflow.tables import WorkflowRun

_STRUCTURAL = {"size", "reference_asset_id", "reference_usage", "unresolved"}
_UNION = {"facts", "must_include", "must_preserve", "forbidden"}
_FIELD_NAMES = {
    "product_description": ("主体", "商品"),
    "purpose": ("用途", "目标", "场景"),
    "style_keywords": ("风格",),
    "composition": ("构图", "摆放"),
    "lighting": ("光",),
    "texts": ("文字", "文案", "标语", "字"),
    "must_preserve": ("保留", "特征"),
    "must_include": ("必须", "包含"),
    "forbidden": ("禁止", "禁用", "限制"),
    "creative_direction": ("创意",),
    "assumptions": ("假设",),
    "facts": ("事实",),
}


def state_of(ctx: Any) -> dict[str, Any]:
    try:
        value = json.loads(ctx.run.requirements_state_json or "{}") if ctx.run else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def current_data(ctx: Any, reference_usage: str) -> dict[str, Any] | None:
    state = state_of(ctx)
    data = state.get("data")
    if (
        isinstance(data, dict)
        and data.get("reference_asset_id") == ctx.project.reference_asset_id
        and data.get("reference_usage") == reference_usage
    ):
        return data
    if not isinstance(data, dict):
        return None
    # A changed reference invalidates observations, not unrelated user decisions.
    sources = state.get("field_sources") or {}
    result = dict(data)
    for key, value in data.items():
        if key in _STRUCTURAL or key in {"purpose", "texts"}:
            continue
        ids = (sources.get(key) or {}).get("evidence_ids") or []
        if ids and all(not item.startswith("reference:") for item in ids):
            continue
        result[key] = [] if isinstance(value, list) else None
    result["facts"] = [
        fact for fact in data.get("facts", []) if fact.get("basis") == "user_declared"
    ]
    result["reference_asset_id"] = ctx.project.reference_asset_id
    result["reference_usage"] = reference_usage
    result["unresolved"] = []
    return result


def _clear_authorized(field: str, text: str) -> bool:
    names = _FIELD_NAMES.get(field, ())
    return any(name in text for name in names) and any(
        word in text
        for word in (
            "没有",
            "无需",
            "不需要",
            "不要",
            "不出现",
            "无",
            "删除",
            "清空",
            "取消",
            "去掉",
            "不保留",
            "随意",
            "自由",
        )
    )


def merge(
    previous: dict[str, Any] | None,
    proposed: dict[str, Any],
    *,
    updates: list[dict[str, Any]],
    evidence: dict[str, str],
    strict: bool,
    prior_sources: dict[str, Any] | None = None,
) -> tuple[BriefData, dict[str, Any]]:
    """Missing values never erase known fields; explicit updates carry provenance."""
    result = {key: value for key, value in proposed.items() if key in BriefData.model_fields}
    if previous:
        result.update(
            {key: value for key, value in previous.items() if key in BriefData.model_fields}
        )
    sources = dict(prior_sources or {})
    if previous:
        for key, value in proposed.items():
            if key in _STRUCTURAL:
                result[key] = value
            elif value not in (None, "", []):
                if strict and previous.get(key) not in (None, "", []):
                    continue
                if key in _UNION:
                    values = list(previous.get(key) or [])
                    for item in value:
                        if item not in values:
                            values.append(item)
                    result[key] = values
                else:
                    result[key] = value
    for update in updates:
        field, operation = update["field"], update["operation"]
        ids = update.get("evidence_ids") or []
        if not ids or any(item not in evidence for item in ids):
            raise ApiError(409, "REQUIREMENT_SOURCE_INVALID", "需求修改缺少有效用户来源")
        if (
            previous is not None
            and operation in {"set", "clear"}
            and evidence
            and next(reversed(evidence)) not in ids
        ):
            raise ApiError(409, "REQUIREMENT_SOURCE_INVALID", "需求修改必须引用本轮用户说明")
        text = "\n".join(evidence[item] for item in ids)
        if operation == "clear":
            if field in _STRUCTURAL or not _clear_authorized(field, text):
                raise ApiError(409, "REQUIREMENT_SOURCE_INVALID", "清空需求需要用户明确说明")
            result[field] = [] if isinstance(result.get(field), list) else None
        elif operation == "set":
            value = update.get("value")
            if (
                field not in _STRUCTURAL
                and value in (None, "", [])
                and result.get(field) not in (None, "", [])
            ):
                raise ApiError(
                    409, "REQUIREMENT_SOURCE_INVALID", "清空已有字段必须使用明确的 clear 操作"
                )
            result[field] = value
        elif operation == "conflict":
            result["unresolved"] = list(dict.fromkeys([*(result.get("unresolved") or []), field]))
        sources[field] = {
            "status": "resolved" if operation in {"set", "clear"} else operation,
            "evidence_ids": ids,
        }
    for key, source in sources.items():
        if source.get("status") in {"unknown", "conflict"}:
            result["unresolved"] = list(dict.fromkeys([*(result.get("unresolved") or []), key]))
    for key, value in result.items():
        if key in sources or value in (None, "", []) or key in _STRUCTURAL:
            continue
        values = value if isinstance(value, list) else [value]
        if not all(isinstance(item, str) for item in values):
            continue
        ids = [
            item_id for item_id, text in evidence.items() if all(item in text for item in values)
        ]
        if ids:
            sources[key] = {"status": "resolved", "evidence_ids": ids}
    try:
        return BriefData.model_validate(result), sources
    except ValidationError as exc:
        raise ApiError(409, "LLM_RESPONSE_INVALID", "需求增量不符合字段契约") from exc


def render_question(question: dict[str, Any]) -> str:
    parsed = ClarificationQuestion.model_validate(question)
    if not parsed.options:
        return parsed.text
    return (
        parsed.text
        + " 请选择："
        + "；".join(f"{option.id}. {option.label}" for option in parsed.options)
        + "。"
    )


def parse_answers(questions: list[dict[str, Any]], reply: str) -> list[dict[str, Any]]:
    raw_reply = reply
    tokens = list(re.finditer(r"(?<!\d)(\d{1,2})\s*[.、:：]?\s*([A-Za-z])(?![A-Za-z])", reply))
    if not tokens and len(questions) == 1 and re.match(r"^\s*[A-Za-z](?=$|[\s，,。；;但])", reply):
        reply = "1" + reply.lstrip()
        tokens = list(re.finditer(r"(\d+)([A-Za-z])", reply))
    answers = []
    seen = set()
    for token in tokens:
        index = int(token[1]) - 1
        if index < 0 or index >= len(questions) or index in seen:
            continue
        question = questions[index]
        option = next(
            (item for item in question["options"] if item["id"] == token[2].upper()), None
        )
        if option is None:
            continue
        seen.add(index)
        answers.append(
            {
                "number": index + 1,
                "question_id": question["id"],
                "field": question["field"],
                "question": question["text"],
                "option": option["id"],
                "answer": option["value"],
                "requires_detail": option["requires_detail"],
                "delegated": option.get("delegates", False),
                "raw_text": raw_reply,
                "topics": _topics(question["field"]),
            }
        )
    return answers


def _topics(field: str) -> list[str]:
    return {
        "texts": ["texts"],
        "must_preserve": ["preserve"],
        "size": ["size"],
        "style_keywords": ["visual"],
        "composition": ["visual"],
        "lighting": ["visual"],
    }.get(field, [])


async def save(
    ctx: Any,
    data: BriefData,
    *,
    sources: dict[str, Any],
    questions: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    decision: dict[str, Any] | None = None,
    question_message_id: str | None = None,
) -> None:
    previous = state_of(ctx)
    state = {
        **previous,
        **(decision or {}),
        "version": 2 if decision is not None or previous.get("version") == 2 else 1,
        "revision": ctx.project.revision,
        "data": data.model_dump(mode="json"),
        "field_sources": sources,
        "questions": questions,
        "answers": [*previous.get("answers", []), *answers][-128:],
        "question_message_id": question_message_id,
    }
    encoded = json.dumps(state, ensure_ascii=False)
    async with write_transaction(ctx.session_factory) as session:
        run = await session.get(WorkflowRun, ctx.run_id)
        if run is not None:
            run.requirements_state_json = encoded
    ctx.run.requirements_state_json = encoded
