"""Persisted conversation policy; submission authority stays in the program."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.errors import ApiError

SOFT_FIELDS = {"purpose", "creative_direction", "style_keywords", "composition", "lighting"}
STOP = re.compile(
    r"先别(?:做|生成|制作|开始|急|动手)|先不(?:做|生成|制作|开始)|不要生成|不生成|不需要.{0,4}生成|别生成|别做|暂不|暂停|先讨论|先聊|先看看|区别|怎么|为什么|如何|哪个好"
)
REQUEST = re.compile(
    r"生成|重做|重新做|制作|做.{0,12}(图|海报)|画.{0,8}(图|钢笔|商品)|直接做|开始做|帮我做|给我做"
)
PAUSE = re.compile(r"先别(?:做|生成|制作|开始|急|动手)|先不(?:做|生成|制作|开始)|不要生成|不生成|不需要.{0,4}生成|别生成|别做|暂不|暂停|先别重新生成")
FEEDBACK = re.compile(r"不好看|没.{0,3}感觉|太乱|不够高级|产品太小|背景太抢|太暗")
DIRECT = re.compile(r"直接(做|生成|制作)|不用问|不必问|你(来定|决定|安排)|帮我选|不知道|随便")


# Positive task signals keep descriptive briefs valid without imperative verbs.
VISUAL_BRIEF = re.compile(r"主图|宣传图|产品图|商品图|海报|详情页|封面|背景|构图|灯光|棚拍|特写|留白|极简|冷白|自然光|电商|poster|product photo", re.I)
EDIT = re.compile(r"换成|替换|改成|调整|改为|去掉|保留|加上|增加|减少|再.{0,6}(一点|一些)|文案|文字|提示词")
DELEGATION = re.compile(r"^(?:(?:那就|都|你来定|你决定|你安排|帮我选|随便|不知道|直接做|直接生成|直接制作|不用问|不必问)[，,。！!\s]*)+$")
SOCIAL = re.compile(r"^(?:(?:你好|您好|嗨|哈喽|早上好|晚上好|谢谢(?:你|您)?|多谢|感谢(?:你|您)?|辛苦了|好的|好吧|收到|明白了|继续|hello|hi|hey|thanks|thank you|ok)[呀啊哦呢啦！!。,.，\s]*)+$", re.I)
CONSULTATION = re.compile(r"你(?:能|可以|会|是|有什么|有啥)|能(?:做什么|帮.{0,6}什么)|可以做什么|支持什么|有什么功能|介绍一下|怎么|如何|为什么|区别|哪个好|讨论|咨询|聊聊|先聊|what can you|who are you|can you explain", re.I)


def conversational(text: str) -> bool:
    """Speech acts that carry no requirement mutation or creation authority."""
    return bool(
        SOCIAL.fullmatch(text.strip()) or CONSULTATION.search(text)
        or re.fullmatch(r"(?:在吗|在不在|有人吗|嗯|嗯嗯|好|哈+)[？?。！!\s]*", text)
    )


def grounded_request(text: str, brief: dict[str, Any] | None = None) -> bool:
    """A model-filled brief needs literal subject evidence in the user's text.

    This also accepts arbitrary product names without requiring creation verbs.
    """
    if conversational(text) or DELEGATION.fullmatch(text.strip()) or STOP.search(text):
        return False
    if REQUEST.search(text) or VISUAL_BRIEF.search(text):
        return True
    subject = (brief or {}).get("product_description")
    if not isinstance(subject, str):
        return False
    return any(
        len(term) >= 2 and term.casefold() in text.casefold()
        for term in re.findall(r"[\w]+", subject)
    )


def deterministic_answer(state: dict[str, Any], text: str) -> bool:
    from app.workflow.requirements import parse_answers

    questions = state.get("questions", [])
    if conversational(text):
        return False
    if parse_answers(questions, text):
        return True
    if DELEGATION.fullmatch(text.strip()):
        return bool(questions) and all(
            q.get("field") in SOFT_FIELDS
            and q.get("purpose") not in {"missing_fact", "conflict"}
            for q in questions
        )
    return any(
        text.strip() in {str(o.get("value", "")), str(o.get("label", ""))}
        for q in questions for o in q.get("options", [])
    )


def intent(previous: dict[str, Any], text: str, message_id: str) -> dict[str, Any]:
    prior = previous.get("generation_intent", {})
    if prior.get("status") == "paused" and message_id == prior.get("superseded_request_message_id"):
        return dict(prior)
    if unbound_option(previous, text):
        return {"status": "discussion", "message_id": message_id}
    if STOP.search(text):
        return {
            "status": "paused",
            "message_id": message_id,
            "superseded_request_message_id": prior.get("superseded_request_message_id")
            or prior.get("message_id"),
        }
    if not previous.get("questions") and FEEDBACK.search(text) and not REQUEST.search(text):
        return {"status": "discussion", "message_id": message_id}
    # A migrated v1 state is context, never fresh creation authority.
    descriptive_request = not prior and grounded_request(text, previous.get("data"))
    if (REQUEST.search(text) and not conversational(text) and not DELEGATION.fullmatch(text.strip())) or descriptive_request:
        return {"status": "requested", "message_id": message_id}
    return dict(prior) if prior else {"status": "discussion", "message_id": message_id}


def choose_questions(
    stored: dict[str, Any],
    proposed: list[dict[str, Any]],
    result: dict[str, Any],
    text: str,
    answered: set[str],
    run_id: str,
    revision: int,
) -> tuple[list[dict[str, Any]], int]:
    rounds = int(stored.get("exploration_rounds", 0))
    topics = set(stored.get("answered_topics", [])) | answered
    delegated = bool(DIRECT.search(text))
    hard = [
        q
        for q in proposed
        if q["field"] not in SOFT_FIELDS or q.get("purpose") in {"missing_fact", "conflict"}
    ]
    soft = [q for q in proposed if q not in hard and q["field"] not in topics]
    # A model-filled design default must not hide a vague user request.
    if (
        not hard
        and not soft
        and not topics
        and not stored.get("data")
        and (REQUEST.search(text) or re.search(r"高级|好看|有质感|有感觉", text))
        and not re.search(r"背景|桌面|深色|浅色|留白|构图|灯光|极简|棚拍|侧光|特写|复古|简洁|冷白|温暖|自然光", text)
    ):
        soft = [
            {
                "id": "purpose",
                "field": "purpose",
                "purpose": "goal",
                "delegatable": True,
                "text": "这张图主要想清楚展示产品，还是营造品牌宣传的氛围？",
                "options": [
                    {
                        "id": "A",
                        "label": "看清产品：干净背景、细节清楚",
                        "value": "商品展示",
                        "requires_detail": False,
                    },
                    {
                        "id": "B",
                        "label": "品牌宣传：突出氛围与质感",
                        "value": "品牌宣传",
                        "requires_detail": False,
                    },
                    {
                        "id": "C",
                        "label": "你帮我选",
                        "value": "商品展示",
                        "requires_detail": False,
                        "delegates": True,
                    },
                ],
            }
        ]
    if soft and soft[0]["id"] == "purpose" and re.search(r"送礼|详情页|主图|社交|海报|首页", text):
        soft = [
            {
                "id": "direction",
                "field": "style_keywords",
                "purpose": "direction",
                "delegatable": True,
                "text": "用途清楚了，你更喜欢哪种画面？",
                "options": [
                    {
                        "id": "A",
                        "label": "干净克制：浅背景，细节清楚",
                        "value": "浅色背景，细节清楚",
                        "requires_detail": False,
                    },
                    {
                        "id": "B",
                        "label": "商务稳重：深背景，产品突出",
                        "value": "深色背景，产品突出",
                        "requires_detail": False,
                    },
                    {
                        "id": "C",
                        "label": "你帮我选",
                        "value": "浅色背景，细节清楚",
                        "requires_detail": False,
                        "delegates": True,
                    },
                ],
            }
        ]
    clear_direction = re.search(
        r"极简|冷白|浅色|深色|居中|留白|[蓝白黑红绿]色?背景|桌面", text
    ) and re.search(r"主图|送礼|宣传|详情|文字|文案", text)
    if delegated or clear_direction or rounds >= 3:
        soft = []
    selected = (hard or soft)[:1]
    if selected and not hard:
        rounds += 1
    for question in selected:
        if question not in hard:
            question["delegatable"] = True
        if question in hard:
            question["delegatable"] = False
            question["options"] = [o for o in question["options"] if not o.get("delegates")]
        content = json.dumps(
            {key: value for key, value in question.items() if key != "id"},
            sort_keys=True,
            ensure_ascii=False,
        )
        question["id"] = str(uuid5(NAMESPACE_URL, f"{run_id}:{revision}:{content}"))
    return selected, rounds


def unbound_option(state: dict[str, Any], text: str) -> bool:
    return not state.get("questions") and bool(
        re.fullmatch(r"\s*(?:(?:\d+\s*[A-Za-z]\s*)+|[A-Za-z])\s*", text)
    )


def validate_answer(ctx: Any, binding: dict[str, Any] | None, text: str) -> None:
    if binding is None:
        return
    from app.workflow.requirements import state_of

    state = state_of(ctx)
    questions = state.get("questions", [])
    question = next((q for q in questions if q["id"] == binding["question_id"]), None)
    if (
        not ctx.direct
        or ctx.run is None
        or ctx.run.stage != "S03"
        or ctx.run.status != "needs_input"
        or state.get("revision") != binding["requirement_revision"]
        or state.get("question_message_id") != binding["message_id"]
        or question is None
    ):
        raise ApiError(409, "QUESTION_EXPIRED", "这个选项已失效，请回答当前问题。")
    option = next((o for o in question["options"] if o["id"] == binding["option_id"]), None)
    from app.workflow.requirements import parse_answers

    matched = any(
        answer["question_id"] == question["id"] and answer["option"] == binding["option_id"]
        for answer in parse_answers(questions, text)
    )
    if option is None or not matched:
        raise ApiError(409, "QUESTION_EXPIRED", "选项与当前问题不一致，请刷新后重试。")


def require_generation(ctx: Any) -> None:
    from app.workflow.requirements import state_of

    state = state_of(ctx)
    # Historical runs retain their existing execution/recovery contract.
    if state.get("version", 1) < 2:
        return
    if (
        state.get("questions")
        or state.get("deferred_questions")
        or state.get("revision") != ctx.project.revision
        or state.get("generation_intent", {}).get("status") != "requested"
    ):
        raise ApiError(409, "GENERATION_NOT_REQUESTED", "请先回答当前问题，或明确告诉我开始制作。")
