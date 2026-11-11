"""Four collaborating roles with natural replies and automatic image delivery.

Historical approval records remain persistence adapters for the execution engine;
they do not introduce a user confirmation or an independent reviewer call.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from app.workflow import alignment, requirements
from app.workflow.contracts import BriefData
from app.workflow.state import (
    add_review,
    latest_artifact,
    reload_context,
    set_stage,
    start_artifact,
)

if TYPE_CHECKING:
    from app.models import Message
    from app.workflow.roles import RoleOutput
    from app.workflow.state import WorkflowContext


async def accept_requirements(
    ctx: WorkflowContext,
    output: RoleOutput,
    previous: dict[str, Any] | None,
    messages: list[Message],
    *,
    size_conflict: bool = False,
    requested_size: tuple[str, float] | None = None,
    reference_usage: str = "none",
) -> None:
    from app.workflow.consolidated import plan_visual
    from app.workflow.stages import s03

    if output.status == "blocked":
        text = output.reply or "这次请求暂时无法完成，请调整描述后再试。"
        await s03._append_assistant(ctx, text, ctx.project.revision)
        await set_stage(ctx, "S03", "blocked", message=text)
        return
    stored = requirements.state_of(ctx)
    if alignment.unbound_option(stored, messages[-1].content):
        text = "目前没有待回答的选项，你可以直接描述想法或需要调整的地方。"
        await s03._append_assistant(ctx, text, ctx.project.revision)
        await set_stage(ctx, "S03", "needs_input", message=text)
        return
    if previous and isinstance(previous.get("payload"), dict):
        previous = previous["payload"]
    result = {k: v for k, v in (previous or {}).items() if k in BriefData.model_fields}
    sources = dict(stored.get("field_sources", {}))
    for key, value in (output.data or {}).items():
        if key not in BriefData.model_fields:
            continue
        if (key not in result or result[key] in (None, "", [])) and sources.get(key, {}).get(
            "status"
        ) != "resolved":
            result[key] = value
    latest = messages[-1]
    # Legacy envelopes can provide a complete brief without deltas. Only a
    # field explicitly mentioned this turn may replace an established value.
    field_mentions = {
        "style_keywords": ("风格", "背景", "配色"),
        "composition": ("构图", "摆放", "居中", "留白"),
        "lighting": ("光线", "灯光", "亮度"),
    }
    for field, words in field_mentions.items():
        value = (output.data or {}).get(field)
        if value not in (None, "", []) and any(word in latest.content for word in words):
            result[field] = value
            sources[field] = {
                "status": "resolved",
                "origin": "user_explicit",
                "evidence_ids": [latest.id],
            }
    locked_fields = {
        q["field"]
        for q in stored.get("questions", [])
        if alignment.DIRECT.search(latest.content)
        and (
            q["field"] not in alignment.SOFT_FIELDS
            or q.get("purpose") in {"conflict", "missing_fact"}
        )
    }
    for field in locked_fields:
        result[field] = (previous or {}).get(
            field,
            []
            if field in {"texts", "facts", "must_preserve", "must_include", "forbidden"}
            else None,
        )
    answers = requirements.parse_answers(stored.get("questions", []), latest.content)
    for answer in answers:
        if answer.get("delegated") and answer["field"] not in alignment.SOFT_FIELDS:
            answer["requires_detail"] = True
    if not answers and len(stored.get("questions", [])) == 1:
        question = stored["questions"][0]
        text = latest.content.strip()
        if (
            alignment.DIRECT.search(text)
            and question["field"] in alignment.SOFT_FIELDS
            and question["field"] not in locked_fields
        ):
            option = next(
                (
                    o
                    for o in question["options"]
                    if not o["requires_detail"] and not o.get("delegates")
                ),
                None,
            )
            value = (
                option["value"]
                if option
                else (output.data or {}).get(question["field"]) or "由助手推荐"
            )
            answers = [
                {
                    "field": question["field"],
                    "answer": value,
                    "requires_detail": False,
                    "delegated": True,
                    "raw_text": text,
                }
            ]
        elif (
            not alignment.DIRECT.search(text)
            and not alignment.STOP.search(text)
            and not re.fullmatch(r"[A-Za-z]", text)
        ):
            answers = [
                {
                    "field": question["field"],
                    "answer": text,
                    "requires_detail": False,
                    "raw_text": text,
                }
            ]
    for answer in answers:
        answer.setdefault(
            "question_id",
            stored.get("questions", [{}])[0].get("id") if stored.get("questions") else None,
        )
        answer.setdefault("number", 1)
        answer["reply_message_id"] = latest.id
        answer["requirement_revision"] = ctx.project.revision
        answer["raw_text"] = latest.content
    answered_fields = {a["field"] for a in answers if not a["requires_detail"]}
    for update in output.requirement_updates:
        if update["field"] in locked_fields:
            continue
        ids = update.get("evidence_ids", [])
        # A restatement of the configured default is program-owned, not a
        # user change. Ignore it without inventing message provenance or
        # marking an unanswered size conflict resolved.
        if (not ids and update["field"] == "size" and update["operation"] == "set"
                and update["value"] == ctx.settings.visual().image_size
                and result.get("size") == update["value"]):
            continue
        if not ids or latest.id not in ids or any(i not in {m.id for m in messages} for i in ids):
            from app.errors import ApiError

            raise ApiError(409, "REQUIREMENT_SOURCE_INVALID", "需求修改必须引用本轮用户说明")
        if update["operation"] == "set":
            result[update["field"]] = update["value"]
        elif update["operation"] == "clear":
            result[update["field"]] = [] if isinstance(result.get(update["field"]), list) else None
        if update["operation"] in {"unknown", "conflict"}:
            sources[update["field"]] = {
                "status": update["operation"],
                "origin": "user_explicit",
                "evidence_ids": [latest.id],
            }
        if update["operation"] in {"set", "clear"}:
            answered_fields.add(update["field"])
            sources[update["field"]] = {
                "status": "resolved",
                "origin": "user_explicit",
                "evidence_ids": [latest.id],
            }
    for answer in answers:
        if answer["requires_detail"]:
            continue
        field, value = answer["field"], answer["answer"]
        suffix = re.sub(r"^\s*(?:\d+\s*)?[A-Za-z]\s*[，,。；;]?\s*", "", latest.content)
        if (
            answer.get("option")
            and len(answers) == 1
            and len(stored.get("questions", [])) == 1
            and suffix
            and suffix != latest.content
            and not re.fullmatch(r"(?:\d+\s*[A-Za-z]\s*)+", suffix)
        ):
            value += f"；补充：{suffix}"
        update = next(
            (
                u
                for u in output.requirement_updates
                if u["field"] == field and u["operation"] in {"set", "clear"}
            ),
            None,
        )
        if update is not None:
            if (
                answer.get("option")
                and len(answers) == 1
                and suffix
                and suffix != latest.content
                and not re.fullmatch(r"(?:\d+\s*[A-Za-z]\s*)+", suffix)
                and suffix not in str(result.get(field))
            ):
                direction = result.get("creative_direction") or ""
                result["creative_direction"] = f"{direction}；用户补充：{suffix}".lstrip("；")[
                    :2000
                ]
                sources["creative_direction"] = {
                    "status": "resolved",
                    "origin": "user_explicit",
                    "evidence_ids": [latest.id],
                }
            continue
        if field in {"must_preserve", "texts", "style_keywords", "must_include", "forbidden"}:
            value = (
                []
                if (field == "texts" and s03._explicit_text_choice(value) is False)
                or (
                    field in {"must_preserve", "must_include", "forbidden"}
                    and re.match(r"^(没有|不需要|无需|无特殊|自由)", value)
                )
                else [value]
            )
        if answer.get("delegated"):
            result.setdefault("assumptions", []).append(f"用户委托选择{field}，建议：{value}")
        result[field] = value
        sources[field] = {
            "status": "resolved",
            "origin": "user_delegated" if answer.get("delegated") else "user_explicit",
            "evidence_ids": [latest.id],
        }
    pending = [*stored.get("questions", []), *stored.get("deferred_questions", [])]
    if (
        len(pending) == 1
        and pending[0]["field"] == "texts"
        and not answers
        and not alignment.STOP.search(latest.content)
        and not alignment.DIRECT.search(latest.content)
        and s03._explicit_text_choice(latest.content) is None
        and not (output.data or {}).get("texts")
        and not re.fullmatch(r"(?:\s*\d+\s*[A-Za-z]\s*)+|\s*[A-Za-z]\s*", latest.content)
    ):
        result["texts"] = [latest.content.strip()]
    if s03._explicit_text_choice(latest.content) is False:
        result["texts"] = []
        answered_fields.add("texts")
        sources["texts"] = {"status": "resolved", "evidence_ids": [latest.id]}
    if any(
        u["field"] == "texts"
        and (u["operation"] == "clear" or u["operation"] == "set" and u["value"] == [])
        for u in output.requirement_updates
    ):
        answered_fields.add("texts")
    questions = [dict(q) for q in output.clarifications if q["field"] not in answered_fields]
    for q in [*stored.get("questions", []), *stored.get("deferred_questions", [])]:
        if (
            q["field"] not in answered_fields
            and (
                q["field"] not in alignment.SOFT_FIELDS
                or q.get("purpose") in {"conflict", "missing_fact"}
            )
            and not any(item["field"] == q["field"] for item in questions)
        ):
            questions.append(dict(q))
    for update in output.requirement_updates:
        if update["field"] in locked_fields:
            continue
        if update["operation"] == "conflict" or (
            update["operation"] == "unknown" and update["field"] not in alignment.SOFT_FIELDS
        ):
            questions = [q for q in questions if q["field"] != update["field"]]
            questions.insert(
                0,
                {
                    "id": "resolve",
                    "field": update["field"],
                    "purpose": "conflict" if update["operation"] == "conflict" else "missing_fact",
                    "text": "请提供希望出现在画面中的文字原文。"
                    if update["field"] == "texts"
                    else "这项要求还不明确，请说明希望保留的内容或优先采用哪一项。",
                    "options": [],
                },
            )
    copy_pending = (
        any(q["field"] == "texts" for q in pending)
        and "texts" not in answered_fields
        and not result.get("texts")
    )
    if copy_pending and not any(q["field"] == "texts" for q in questions):
        questions.extend(q for q in pending if q["field"] == "texts")
    if result.get("texts"):
        questions = [
            q for q in questions if q["field"] != "texts" or q.get("purpose") == "conflict"
        ]
    if result.get("product_description"):
        questions = [
            q
            for q in questions
            if q["field"] != "product_description" or q.get("purpose") == "conflict"
        ]
    for answer in answers:
        if answer["requires_detail"] and not any(q["field"] == answer["field"] for q in questions):
            questions.append(
                {
                    "id": answer["question_id"] + "-detail",
                    "field": answer["field"],
                    "text": "请提供希望出现在画面中的文字原文。"
                    if answer["field"] == "texts"
                    else "请补充所选项目的具体内容。",
                    "options": [],
                }
            )
    if not result.get("product_description") and not any(
        q["field"] == "product_description" for q in questions
    ):
        questions.insert(
            0,
            {
                "id": "subject",
                "field": "product_description",
                "text": "你想画什么主体？",
                "options": [],
            },
        )
    if size_conflict:
        # Negotiating a supported size must retain the other unanswered facts.
        questions = [
            {
                "id": "size",
                "field": "size",
                "text": s03._size_question(
                    s03._configured_size(ctx), requested_size[0] if requested_size else "原比例"
                ),
                "options": [],
            },
            *(question for question in questions if question["field"] != "size"),
        ]
    questions = [
        q
        for q in questions
        if q.get("purpose") in {"conflict", "missing_fact"}
        or q["field"] not in {"texts", "must_preserve"}
        or (
            q["field"] == "texts"
            and (copy_pending or any(w in latest.content for w in ("文字", "文案", "标语", "名称")))
        )
        or any(a["field"] == q["field"] and a["requires_detail"] for a in answers)
    ]
    generation_intent = alignment.intent({**stored, "data": result}, latest.content, latest.id)
    if output.conversation_only and generation_intent["status"] != "paused":
        generation_intent = {"status": "discussion", "message_id": latest.id}
    all_questions = list(questions)
    questions, rounds = alignment.choose_questions(
        {**stored, "data": previous} if previous else stored,
        questions,
        result,
        latest.content,
        answered_fields,
        ctx.run_id,
        ctx.project.revision,
    )
    decision = {
        "deferred_questions": [
            q
            for q in all_questions
            if (
                q["field"] not in alignment.SOFT_FIELDS
                or q.get("purpose") in {"conflict", "missing_fact"}
            )
            and q not in questions
        ],
        "generation_intent": generation_intent,
        "exploration_rounds": rounds,
        "answered_topics": list(
            dict.fromkeys([*stored.get("answered_topics", []), *answered_fields])
        ),
    }
    for field, value in result.items():
        if (
            field not in sources
            and value not in (None, "", [])
            and field not in {"size", "reference_asset_id", "reference_usage", "unresolved"}
        ):
            values = value if isinstance(value, list) else [value]
            explicit = all(isinstance(item, str) and item in latest.content for item in values)
            observed = field == "facts" and any(
                isinstance(item, dict) and item.get("basis") != "user_declared" for item in values
            )
            sources[field] = {
                "status": "resolved" if explicit else "suggested",
                "origin": "user_explicit"
                if explicit
                else "reference_observed"
                if observed
                else "assistant_suggested",
                "evidence_ids": [latest.id]
                if explicit
                else [f"reference:{ctx.project.reference_asset_id}"]
                if observed
                else [],
            }
    for field in BriefData.model_fields:
        if field not in result:
            result[field] = (
                []
                if field
                in {
                    "facts",
                    "style_keywords",
                    "texts",
                    "must_include",
                    "must_preserve",
                    "forbidden",
                    "assumptions",
                    "unresolved",
                }
                else None
            )
    result["size"] = s03._configured_size(ctx)
    result["reference_asset_id"] = ctx.project.reference_asset_id
    result["reference_usage"] = reference_usage
    result["unresolved"] = [q["field"] for q in questions]
    data = BriefData.model_validate(result)
    await requirements.save(
        ctx, data, sources=sources, questions=questions, answers=answers, decision=decision
    )
    if questions:
        text = "\n\n".join(requirements.render_question(q) for q in questions)
        message = await s03._append_assistant(ctx, text, ctx.project.revision)
        await requirements.save(
            ctx,
            data,
            sources=sources,
            questions=questions,
            answers=[],
            decision=decision,
            question_message_id=message.id,
        )
        await set_stage(ctx, "S03", "needs_input", issue_code="MISSING_INPUT", message=text)
        return
    if generation_intent["status"] != "requested":
        text = output.reply or "我会保留这些想法。你可以继续讨论，想制作时告诉我开始做。"
        await s03._append_assistant(ctx, text, ctx.project.revision)
        await set_stage(ctx, "S03", "needs_input", message=text)
        return
    defaults = {
        "purpose": "商品展示",
        "style_keywords": ["自然商品摄影"],
        "composition": "以主体为中心，保持画面简洁",
        "lighting": "柔和自然光",
    }
    for field, value in defaults.items():
        if not result.get(field):
            result["assumptions"].append(f"未指定{field}，设计默认：{value}")
            sources[field] = {
                "status": "suggested",
                "origin": "assistant_suggested",
                "evidence_ids": [],
            }
    result["purpose"] = result.get("purpose") or "商品展示"
    result["style_keywords"] = result.get("style_keywords") or ["自然商品摄影"]
    result["composition"] = result.get("composition") or "以主体为中心，保持画面简洁"
    result["lighting"] = result.get("lighting") or "柔和自然光"
    result["unresolved"] = []
    data = BriefData.model_validate(result)
    await requirements.save(ctx, data, sources=sources, questions=[], answers=[], decision=decision)
    artifact = await start_artifact(ctx, "brief_draft", data)
    await add_review(
        ctx, artifact, reviewer_role="requirements_assistant", decision="pass", checks=[]
    )
    direction = "、".join(data.style_keywords) or data.composition or "简洁画面"
    suggested = sources.get("style_keywords", {}).get("origin") in {
        "assistant_suggested",
        "user_delegated",
    }
    recap = f"我会以{data.product_description[:160]}为主体，按{data.purpose}安排画面。"
    recap += f"{'我建议采用' if suggested else '采用你选择的'}{direction[:180]}，让主体更清楚。"
    recap += ("保留文字：" + "、".join(data.texts)[:240] + "。") if data.texts else "先不加文字。"
    recap += "接下来整理提示词给你检查，确认后才会生成图片。"
    await s03._append_assistant(ctx, recap, ctx.project.revision)
    await set_stage(ctx, "S04", "active")
    await plan_visual(ctx)


async def accept_generation(ctx: WorkflowContext) -> None:
    from app.workflow.stages import s06

    alignment.require_generation(ctx)
    artifact = await latest_artifact(ctx, "final_brief", status="approved")
    await s06.handle(
        ctx,
        "confirm_requirements",
        {
            "request_id": str(
                uuid5(
                    NAMESPACE_URL, f"direct:{ctx.run_id}:{ctx.project.revision}:{artifact.digest}"
                )
            ),
            "artifact_id": artifact.id,
            "artifact_digest": artifact.digest,
            "requirement_revision": ctx.project.revision,
        },
    )


async def deliver(ctx: WorkflowContext) -> None:
    from app.workflow.stages import s10

    for action in ("approve_delivery", "publish_delivery"):
        fresh = await reload_context(ctx)
        if action in s10.available(fresh):
            await s10.handle(fresh, action, {})


async def _reuse_saved_clarification(ctx: WorkflowContext) -> bool:
    """Revalidate only a saved clarification on manual recovery, never execute it."""
    import json

    from sqlalchemy import select

    from app.workflow.contracts import ContractError, validate_role_output
    from app.workflow.roles import RoleOutput, _normalize_envelope_placement
    from app.workflow.stages import s03
    from app.workflow.state import record_diagnostic
    from app.workflow.tables import RoleCall

    async with ctx.session_factory() as session:
        call = await session.scalar(select(RoleCall).where(
            RoleCall.run_id == ctx.run_id, RoleCall.role == "requirements_assistant",
            RoleCall.stage == "S03",
        ).order_by(RoleCall.started_at.desc()).limit(1))
    if call is None or call.status != "invalid" or call.error_code != "CONTRACT_INVALID":
        return False
    messages = await s03._user_messages(ctx)
    try:
        request = json.loads(call.request_json)["payload"]
        task, inputs = request["task"], request["input_data"]
        if (not messages or task["revision"] != ctx.project.revision
                or task["run_id"] != ctx.run_id
                or inputs.get("reference_asset_id") != ctx.project.reference_asset_id
                or inputs.get("configured_size") != ctx.settings.visual().image_size
                or inputs.get("user_messages") != [
                    {"message_id": m.id, "text": m.content} for m in messages
                ]):
            return False
        envelope = validate_role_output("requirements_assistant", _normalize_envelope_placement(
            json.loads(call.response_json), role="requirements_assistant"
        ))
    except (ValueError, TypeError, KeyError, ContractError):
        return False
    if (envelope.status != "needs_input" or envelope.data is None
            or envelope.data.scope != "image_request" or envelope.data.kind != "clarification"):
        return False
    dumped = envelope.model_dump(mode="json")
    ctx.prepared_requirements = (messages[-1].content, RoleOutput(
        role="requirements_assistant", status="needs_input", data=dumped["data"],
        issues=dumped["issues"], questions=dumped["questions"],
        model_fingerprint=call.model_fingerprint, role_call_id=call.id, attempt=call.attempt,
    ))
    await record_diagnostic(ctx, "saved_clarification_reused", {"role_call_id": call.id})
    return True


async def resume(ctx: WorkflowContext, stage: str) -> None:
    """Continue a saved producer or reviewer failure under the current roles."""
    from app.workflow.consolidated import plan_visual
    from app.workflow.stages import s02, s03, s07, s09, s10
    from app.workflow.state import set_intent

    if stage not in {"S01", "S02", "S03", "S04", "S05", "S06", "S07", "S09", "S10"}:
        from app.errors import ApiError

        raise ApiError(
            409, "RECOVERY_UNSUPPORTED", "当前检查点无法继续，请重新描述需要处理的图片。"
        )

    await set_stage(ctx, stage, "active")
    if stage in {"S01", "S03"}:
        await set_intent(ctx, "image_request", "new_request")
        if ctx.project.reference_asset_id and await s03.current_reference_report(ctx) is None:
            await s02.analyze(ctx)
        else:
            if stage == "S03":
                await _reuse_saved_clarification(ctx)
            await s03._compose(ctx, "compose_brief", None, record_user_text=False)
    elif stage == "S02":
        await s02.analyze(ctx)
    elif stage in {"S04", "S05", "S06"}:
        await plan_visual(ctx)
    elif stage == "S07":
        await s07._compile(ctx, None)
    elif stage in {"S09", "S10"}:
        if stage == "S09":
            await s09.handle(ctx, "review_candidate", {})
        fresh = await reload_context(ctx)
        if fresh.run.stage == "S10" and fresh.run.status == "active":
            await s10.handle(fresh, "prepare_delivery", {})
        fresh = await reload_context(ctx)
        if fresh.run.stage == "S10":
            await deliver(fresh)
