from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Message, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import requirements, templates
from app.workflow.adjudication import (
    ProtocolIssue,
    gate_mode_for_target,
    governance_ruleset,
    review_output,
)
from app.workflow.contracts import (
    BriefData,
    ContractError,
    ReferenceData,
    digest_payload,
)
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    add_review,
    approved_artifact,
    latest_artifact,
    latest_project_artifact,
    record_diagnostic,
    revoke_downstream_approvals,
    set_artifact_status,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, DiagnosticEvent, WorkflowRun

STAGE = "S03"
ACTIONS = [
    "compose_brief",
    "answer_requirements_clarification",
    "revise_brief_draft",
]

# The independent reviewer attests every applicable G03 rule. The shared
# reviewer prompt also asks for broader Gnn_ governance checks; those extra
# findings are advisory and must not stop an otherwise valid requirements
# review. A pass with a missing, failed or malformed required check never
# releases a draft.
REVIEW_RULE_IDS = (
    "G03_STRUCTURE",
    "G03_SUBJECT_PURPOSE",
    "G03_SOURCE_BASIS",
    "G03_TEXTS",
    "G03_CONSTRAINTS",
    "G03_SIZE",
    "G03_UNRESOLVED",
)
# The server catalogue decides which rules are mandatory, which governance
# observations are advisory and what evidence each rule accepts. A reviewer
# cannot add, rename or waive a rule by returning a different list.
_RULESET = governance_ruleset(
    "requirement_draft",
    tuple((rule_id, "需求草稿核验") for rule_id in REVIEW_RULE_IDS),
    evidence_types=("artifact", "reference"),
)

_MAX_QUESTIONS_CHARS = 800
_MAX_CANDIDATES = 3

REFERENCE_USAGES = ("product", "style", "composition")
_REFERENCE_EVIDENCE_PREFIX = "reference:"

_COLOR_TERMS = (
    "白色",
    "黑色",
    "红色",
    "蓝色",
    "绿色",
    "黄色",
    "灰色",
    "粉色",
    "紫色",
    "橙色",
    "棕色",
    "米色",
    "金色",
    "银色",
)
_COUNT_WORDS = {
    "一个": 1,
    "一只": 1,
    "一件": 1,
    "一瓶": 1,
    "一杯": 1,
    "两个": 2,
    "两只": 2,
    "两件": 2,
    "两瓶": 2,
    "两杯": 2,
    "三个": 3,
    "三只": 3,
    "三件": 3,
    "三瓶": 3,
    "三杯": 3,
}


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status == "active":
        # Without a configured square output size a ready draft cannot satisfy
        # the v2.0.0 contract, so the action is not offered.
        if not (ctx.project.image_size or ctx.settings.visual().image_size):
            return []
        # The original S01 user statement is already persisted; composing the
        # brief therefore does not require the user to repeat it.
        return [
            {
                "id": "compose_brief",
                "label": "整理需求卡",
                "requires_text": False,
                "text_label": None,
            }
        ]
    if ctx.run.status in {"needs_input", "blocked"}:
        return [
            {
                "id": "answer_requirements_clarification",
                "label": "回答需求问题",
                "requires_text": True,
                "text_label": "补充当前缺少的商品主体、用途或事实",
            }
        ]
    return []


def _invalid_service() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE"))


def _review_unavailable() -> ApiError:
    return ApiError(503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE"))


def _review_invalid(message: str) -> ApiError:
    return ApiError(409, "REVIEW_INVALID", message)


def _review_rejected() -> ApiError:
    return ApiError(409, "REVIEW_REJECTED", templates.render("REVIEW_REJECTED"))


def _require_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip():
        raise validation_error("消息内容不能为空")
    if len(value) > 20_000:
        raise validation_error("消息内容过长")
    return value


def _optional_text(payload: dict[str, Any]) -> str | None:
    value = payload.get("text")
    if value is None:
        return None
    if not isinstance(value, str):
        raise validation_error("消息内容不合法")
    if len(value) > 20_000:
        raise validation_error("消息内容过长")
    return value if value.strip() else None


async def _user_messages(ctx: WorkflowContext) -> list[Message]:
    async with read_session(ctx.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.project_id == ctx.project_id,
                        Message.role == "user",
                        Message.turn_kind == "workflow",
                    )
                    .order_by(Message.sequence_no.asc())
                )
            ).all()
        )


async def _workflow_messages(ctx: WorkflowContext) -> list[Message]:
    async with read_session(ctx.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.project_id == ctx.project_id,
                        Message.turn_kind == "workflow",
                    )
                    .order_by(Message.sequence_no.asc())
                )
            ).all()
        )


async def _conversation_messages(
    ctx: WorkflowContext, *, include_conversation: bool = False
) -> list[dict[str, str]]:
    """Return a bounded, chronological transcript for the consultant.

    The role call is stateless. Without the assistant turns, a reply like "A"
    has no meaning and the model silently re-asks the question it just showed.
    Keep the most recent messages within a small payload budget.
    """
    if include_conversation:
        async with read_session(ctx.session_factory) as session:
            messages = list((await session.scalars(
                select(Message).where(Message.project_id == ctx.project_id)
                .order_by(Message.sequence_no.desc()).limit(48)
            )).all())
        messages.reverse()
    else:
        messages = await _workflow_messages(ctx)
    selected: list[dict[str, str]] = []
    total = 0
    for message in reversed(messages):
        text = message.content.strip()
        if not text:
            continue
        text = text[:3000]
        if selected and (len(selected) >= 24 or total + len(text) > 12_000):
            break
        selected.append({"role": message.role, "text": text})
        total += len(text)
    selected.reverse()
    return selected


async def _pending_clarification(ctx: WorkflowContext) -> str | None:
    """Return the latest assistant clarification shown to the user.

    A short reply such as "A" or "米色" only means something in the context of
    the question and its example options. The consultant must see that turn;
    passing user messages alone makes option-only answers impossible to resolve.
    """
    if ctx.direct:
        questions = requirements.state_of(ctx).get("questions", [])
        return "\n".join(requirements.render_question(q) for q in questions) or None
    async with read_session(ctx.session_factory) as session:
        message = await session.scalar(
            select(Message)
            .where(
                Message.project_id == ctx.project_id,
                Message.role == "assistant",
                Message.turn_kind == "workflow",
                Message.content.contains("请补充以下信息"),
            )
            .order_by(Message.sequence_no.desc())
            .limit(1)
        )
    if message is None or not message.content.strip():
        return None
    return message.content.strip()[:4000]


_QUESTION_LINE = re.compile(r"^\s*(\d{1,2})[.．]\s*(.+?)\s*$")
_OPTION_LINE = re.compile(r"(?<![A-Za-z])([A-Za-z])(?:[.．、:：]\s*|\s+)([^；;。]+)")
_ANSWER_TOKEN = re.compile(r"(?<!\d)(\d{1,2})\s*[.、:：]?\s*([A-Za-z])(?![A-Za-z])")
_ANSWER_REQUESTS_MORE = (
    "请提供",
    "请说明",
    "请给出",
    "请补充",
    "请直接说明",
    "请先",
    "另行",
    "其他",
    "以上都不是",
)


def _clarification_questions(pending: str | None) -> dict[int, dict[str, Any]]:
    """Extract numbered questions and their example options from a report."""
    if not pending:
        return {}
    start = pending.find("请补充以下信息")
    if start < 0:
        return {}
    section = pending[start:]
    end = section.find("回复方式")
    if end >= 0:
        section = section[:end]
    questions: dict[int, dict[str, Any]] = {}
    for line in section.splitlines():
        match = _QUESTION_LINE.match(line)
        if match is None:
            continue
        number = int(match.group(1))
        text = match.group(2).strip()
        options = {
            option.group(1).upper(): option.group(2).strip()
            for option in _OPTION_LINE.finditer(text)
        }
        questions[number] = {"text": text, "options": options}
    return questions


def _question_topics(question: str) -> set[str]:
    # Questions often explain consequences after the example ("...，这决定价格区
    # 的位置"). Classify the ask itself, not the whole paragraph, so a size
    # question is not also treated as a price question.
    text = question.split("例如", 1)[0]
    if "必须保留" in text or ("保留" in text and "特征" in text):
        return {"preserve"}
    if any(marker in text for marker in ("画面文字", "促销标语", "文案", "标语")):
        return {"texts"}
    if any(marker in text for marker in ("尺寸", "比例", "画布")):
        return {"size"}
    if any(marker in text for marker in ("款式", "风格", "构图", "光影")):
        return {"visual"}
    if "价格" in text:
        return {"price"}
    return set()


def _answer_is_complete(answer: str) -> bool:
    text = answer.strip()
    return bool(text) and not any(marker in text for marker in _ANSWER_REQUESTS_MORE)


def _clarification_answers(
    pending: str | None, reply: str | None
) -> list[dict[str, Any]]:
    """Resolve ``1A 2B 3C`` against the numbered questions it answers.

    The model is good at writing questions but unreliable at counting free-form
    answer positions. Resolving the numbers here prevents an answer from being
    silently shifted to a later question when the user omits one.
    """
    questions = _clarification_questions(pending)
    if not questions or not reply:
        return []
    answers: list[dict[str, Any]] = []
    seen: set[int] = set()
    for match in _ANSWER_TOKEN.finditer(reply):
        number = int(match.group(1))
        question = questions.get(number)
        if question is None or number in seen:
            continue
        letter = match.group(2).upper()
        answer = question["options"].get(letter)
        if not answer:
            continue
        seen.add(number)
        answers.append(
            {
                "number": number,
                "question": question["text"][:300],
                "option": letter,
                "answer": answer[:300],
                "topics": sorted(_question_topics(question["text"])),
            }
        )
    return answers


async def _clarification_history(ctx: WorkflowContext) -> list[dict[str, Any]]:
    """Resolve every previous numbered reply, not just the latest one.

    The model has already forgotten an answer once when the next report omitted
    it. Keeping the resolved topics for the whole S03 conversation lets the
    report and its questions stay consistent even when the model's partial draft
    is incomplete.
    """
    messages = await _workflow_messages(ctx)
    stored = requirements.state_of(ctx)
    history: list[dict[str, Any]] = list(stored.get("answers") or [])
    if stored.get("questions"):
        for message in messages:
            if message.role == "user" and message.project_revision > stored.get("revision", -1):
                history.extend(
                    {**answer, "reply_message_id": message.id}
                    for answer in requirements.parse_answers(stored["questions"], message.content)
                )
    pending: str | None = None
    for message in messages:
        if message.role == "assistant" and "请补充以下信息" in message.content:
            pending = message.content
            continue
        if message.role == "user" and pending is not None:
            history.extend(
                {**answer, "reply_message_id": message.id}
                for answer in _clarification_answers(pending, message.content)
            )
            pending = None
    deduplicated = {}
    for answer in history:
        key = (answer.get("reply_message_id"), answer.get("number"))
        if key not in deduplicated:
            deduplicated[key] = answer
    return list(deduplicated.values())


def _requirement_evidence(messages: list[Message], answers: list[dict[str, Any]]) -> dict[str, str]:
    evidence = {message.id: message.content for message in messages}
    for answer in answers:
        message_id = answer.get("reply_message_id")
        if message_id in evidence and not answer.get("requires_detail"):
            evidence[message_id] += "\n已绑定选项：" + str(answer.get("answer", ""))
    return evidence


def _explicit_text_choice(text: str) -> bool | None:
    # Read the latest explicit text decision so a later amendment can replace it.
    for clause in reversed(re.split(r"[\n。；;]", text)):
        if any(marker in clause for marker in (
            "不要文字", "不要任何文字", "不要加字", "不需要文字",
            "不出现文字", "不出现任何文字", "不包含文字", "无文字", "无画面文字",
            "不加字", "不要文案", "无文案",
        )):
            return False
        if any(marker in clause for marker in ("文字", "文案", "标语", "加字", "写上")):
            return True
    return None


def _resolved_topics(answers: list[dict[str, Any]] | None) -> set[str]:
    resolved: set[str] = set()
    for answer in answers or []:
        if answer.get("requires_detail") or not _answer_is_complete(str(answer.get("answer", ""))):
            continue
        resolved.update(str(topic) for topic in answer.get("topics", []))
    return resolved


def _drop_resolved_questions(
    questions: list[str], answers: list[dict[str, Any]] | None, user_text: str = ""
) -> list[str]:
    resolved = _resolved_topics(answers)
    if _explicit_text_choice(user_text) is False:
        resolved.add("texts")
    if not resolved:
        return questions
    remaining: list[str] = []
    for question in questions:
        topics = _question_topics(question)
        if topics and topics <= resolved:
            continue
        remaining.append(question)
    return remaining


async def _record_user_text(ctx: WorkflowContext, text: str) -> Message:
    """Persist the exact additional user text and advance the requirement version."""
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        project.revision += 1
        project.updated_at = now
        highest = await session.scalar(
            select(func.max(Message.sequence_no)).where(
                Message.project_id == ctx.project_id
            )
        )
        message = Message(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest or 0) + 1,
            role="user",
            content=text,
            project_revision=project.revision,
            brief_snapshot_json=None,
            operation_id=None,
            turn_kind="workflow",
            created_at=now,
        )
        session.add(message)
        run = await session.get(WorkflowRun, ctx.run_id)
        if run is not None:
            run.requirement_revision = project.revision
            run.updated_at = now
        await session.flush()
    ctx.project.revision = project.revision
    if ctx.run is not None:
        ctx.run.requirement_revision = project.revision
    # A new user statement creates a new requirement version; no downstream
    # approval may survive it.
    from app.prompt_preparation import invalidate_awaiting

    await invalidate_awaiting(ctx, reason="requirements_changed")
    await revoke_downstream_approvals(ctx, "requirements_changed")
    return message


async def _sync_requirement_revision(ctx: WorkflowContext) -> None:
    if ctx.run is None or ctx.run.requirement_revision == ctx.project.revision:
        return
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await session.get(WorkflowRun, ctx.run.id)
        if run is None:
            raise conflict("WORKFLOW_NOT_STARTED", "流程尚未开始")
        run.requirement_revision = ctx.project.revision
        run.updated_at = now
        await session.flush()
    ctx.run.requirement_revision = ctx.project.revision


async def _supersede_prior_draft(ctx: WorkflowContext) -> None:
    artifact = await latest_artifact(ctx, "brief_draft")
    if artifact is not None and artifact.status in {"candidate", "approved"}:
        await set_artifact_status(ctx, artifact.id, "superseded")


def _configured_size(ctx: WorkflowContext) -> str:
    size = (ctx.project.image_size or ctx.settings.visual().image_size or "").strip()
    if not size:
        raise ApiError(
            503,
            "CAPABILITY_NOT_READY",
            templates.render(
                "CAPABILITY_NOT_READY",
                capability_label=templates.capability_label("IMAGE_GEN") or "",
            ),
        )
    return size


def _requested_size(text: str) -> tuple[str, float] | None:
    matches = list(re.finditer(r"(?<!\d)(\d{1,5})\s*([:：xX×])\s*(\d{1,5})(?!\d)", text))
    if not matches:
        return None
    match = matches[-1]
    width, height = int(match[1]), int(match[3])
    if width <= 0 or height <= 0:
        return None
    return match[0], width / height


def _size_question(size: str, requested: str) -> str:
    return (
        f"当前输出仅支持 {size}，与你的 {requested} 要求不一致。"
        f"请选择：A. 明确接受 {size}，并按此尺寸调整构图；"
        "B. 保留原比例，暂停本次流程。"
    )


def _text_authorized(messages: list[Message]) -> bool:
    text = "\n".join(message.content for message in messages)
    explicit_choice = _explicit_text_choice(text)
    if explicit_choice is not None:
        return explicit_choice
    for marker in (
        "不要文字",
        "不要任何文字",
        "不要加字",
        "不需要文字",
        "无文字",
        "不加字",
        "不要文案",
    ):
        if marker in text:
            return False
    for marker in (
        "文案",
        "文字",
        "标题",
        "标语",
        "字样",
        "加字",
        "写上",
        "写明",
        "注明",
        "标注",
        "排版",
        "字体",
    ):
        if marker in text:
            return True
    return bool(re.search(r"[“\"'][^”\"']{1,200}[”\"']", text))


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return _json_dict(artifact.payload_json)


def _reference_evidence_texts(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Map image evidence ids to the clear observations the report actually made.

    Only clear observations may support an image_observed fact. An uncertain
    observation stays an uncertainty and can never be promoted to a fact.
    """
    mapping: dict[str, list[str]] = {}
    for bucket in ("observations", "texts"):
        items = payload.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if bucket == "observations" and item.get("confidence") != "clear":
                continue
            if bucket == "texts" and item.get("legibility") != "clear":
                continue
            text = (
                item.get("description")
                if bucket == "observations"
                else item.get("text")
            )
            if not isinstance(text, str) or not text.strip():
                continue
            for evidence_id in item.get("evidence_ids") or []:
                if isinstance(evidence_id, str) and evidence_id:
                    mapping.setdefault(evidence_id, []).append(text)
    return mapping


def _reference_evidence_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for bucket in ("observations", "texts"):
        items = payload.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            values = item.get("evidence_ids")
            if isinstance(values, list):
                ids.extend(value for value in values if isinstance(value, str))
    return ids


async def current_reference_report(
    ctx: WorkflowContext,
) -> tuple[Artifact, dict[str, Any]] | None:
    """Return the approved report only when it describes the current image.

    The report binds itself to the original file through the server-issued
    evidence id reference:<asset_id>. Every observation and text item must cite
    that exact id, so replacing the image cannot leave an older report usable.
    An approved older revision never overrides a newer report candidate.
    """
    asset_id = ctx.project.reference_asset_id
    if not asset_id:
        return None
    artifact = await approved_artifact(ctx, "reference_report")
    if artifact is None:
        # A new version run has no copied report of its own. Reuse the latest
        # approved report only when its evidence binds to the current file.
        artifact = await latest_project_artifact(
            ctx, "reference_report", status="approved"
        )
    if artifact is None:
        return None
    expected = _REFERENCE_EVIDENCE_PREFIX + asset_id
    latest = await latest_artifact(ctx, "reference_report")
    if latest is not None and latest.id != artifact.id:
        # A newer candidate for the current image means the approved report is
        # no longer current. A late candidate from an older image is irrelevant
        # and must not disable the valid current approval.
        latest_ids = _reference_evidence_ids(_artifact_payload(latest))
        if any(item == expected for item in latest_ids):
            return None
    try:
        data = ReferenceData.model_validate(_artifact_payload(artifact)).model_dump(
            mode="json"
        )
    except ContractError:
        return None
    if digest_payload(data) != artifact.digest:
        return None
    usage = data.get("reference_usage")
    if usage not in REFERENCE_USAGES:
        return None
    observations = data.get("observations")
    if not isinstance(observations, list) or not observations:
        return None
    evidence = _reference_evidence_ids(data)
    if not evidence or any(item != expected for item in evidence):
        return None
    return artifact, data


def _reference_approved_input(
    artifact: Artifact, payload: dict[str, Any], reference_asset_id: str | None
) -> dict[str, Any]:
    return {
        "kind": "reference_report",
        "artifact_id": artifact.id,
        "digest": artifact.digest,
        "reference_asset_id": reference_asset_id,
        "payload": payload,
    }


def _reference_conflict_hints(
    messages: list[Message], payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Surface obvious color/quantity disagreements to the requirements role.

    The hint is not evidence and never becomes a fact by itself. It only tells
    the role that both sources must be clarified instead of silently choosing
    one as the confirmed value.
    """
    if not payload:
        return []
    user_text = "\n".join(message.content for message in messages)
    hints: list[dict[str, Any]] = []
    reference_colors: set[str] = set()
    for item in payload.get("observations") or []:
        if not isinstance(item, dict) or item.get("kind") != "color":
            continue
        if item.get("confidence") != "clear":
            continue
        description = item.get("description")
        if isinstance(description, str):
            reference_colors.update(
                color for color in _COLOR_TERMS if color in description
            )
    user_colors = {color for color in _COLOR_TERMS if color in user_text}
    if reference_colors and user_colors and not reference_colors & user_colors:
        hints.append(
            {
                "kind": "color",
                "reference_values": sorted(reference_colors),
                "user_values": sorted(user_colors),
                "instruction": "颜色不一致，必须集中询问用户，不得直接选定一方。",
            }
        )
    reference_counts: set[int] = set()
    for item in payload.get("observations") or []:
        if not isinstance(item, dict) or item.get("kind") != "quantity":
            continue
        description = item.get("description")
        if not isinstance(description, str):
            continue
        for word, count in _COUNT_WORDS.items():
            if word in description:
                reference_counts.add(count)
        for match in re.finditer(r"(?<![0-9])([1-9])(?![0-9])", description):
            reference_counts.add(int(match.group(1)))
    user_counts: set[int] = set()
    for word, count in _COUNT_WORDS.items():
        if word in user_text:
            user_counts.add(count)
    for match in re.finditer(r"(?<![0-9])([1-9])(?![0-9])", user_text):
        user_counts.add(int(match.group(1)))
    if reference_counts and user_counts and not reference_counts & user_counts:
        hints.append(
            {
                "kind": "quantity",
                "reference_values": sorted(reference_counts),
                "user_values": sorted(user_counts),
                "instruction": "数量不一致，必须集中询问用户，不得直接选定一方。",
            }
        )
    return hints


def _validate_facts(
    data: BriefData,
    allowed: set[str],
    *,
    has_reference: bool,
    message_ids: set[str],
    reference_evidence: str | None,
    reference_texts: dict[str, list[str]],
) -> None:
    for fact in data.facts:
        if not set(fact.evidence_ids) <= allowed:
            raise _invalid_service()
        if fact.basis == "image_observed":
            if not has_reference or reference_evidence is None:
                raise _invalid_service()
            if any(item != reference_evidence for item in fact.evidence_ids):
                raise _invalid_service()
            supported = [
                text
                for item in fact.evidence_ids
                for text in reference_texts.get(item, [])
            ]
            if not any(
                _normalize_text(fact.text) in _normalize_text(text)
                for text in supported
            ):
                raise _invalid_service()
        else:
            # user_declared facts cite the actual user message, never the image
            # report artifact or an image evidence id.
            if any(item not in message_ids for item in fact.evidence_ids):
                raise _invalid_service()


def _validate_partial_draft(
    data: BriefData,
    *,
    allowed: set[str],
    size: str,
    reference_asset_id: str | None,
    reference_usage: str,
    has_reference: bool,
    text_authorized: bool,
    message_ids: set[str],
    reference_evidence: str | None,
    reference_texts: dict[str, list[str]],
) -> None:
    _validate_text_hygiene(data)
    # A partial brief may retain an unsupported user size while negotiating.
    # Ready briefs still enforce the execution size in _validate_ready_draft.
    if data.reference_asset_id != reference_asset_id:
        raise _invalid_service()
    if data.reference_usage != reference_usage:
        raise _invalid_service()
    if data.texts and not text_authorized:
        raise _invalid_service()
    _validate_facts(
        data,
        allowed,
        has_reference=has_reference,
        message_ids=message_ids,
        reference_evidence=reference_evidence,
        reference_texts=reference_texts,
    )


_NO_PRODUCT_MARKERS = (
    "不含任何商品",
    "不含商品",
    "不包含商品",
    "不出现商品",
    "不展示商品",
    "不需要商品",
    "无需商品",
    "没有商品",
    "无任何商品",
    "无商品",
    "不含任何产品",
    "不含产品",
    "不包含产品",
    "不出现产品",
    "不展示产品",
    "没有产品",
    "无产品",
    "只有人物",
    "仅有人物",
    "只呈现人物",
    "仅呈现人物",
    "纯人物",
)


def _no_product_text(data: BriefData) -> str | None:
    """Return an explicit no-product statement when the user asked for a pure scene.

    The brief schema allows product_description to be null while a request is
    still being clarified. A ready draft, however, must carry a nonempty subject
    for every downstream stage. When the user has explicitly chosen a pure
    portrait/scene with no product, preserve that decision as a concrete string
    instead of treating the missing product as a technical failure.
    """
    candidates: list[Any] = [
        data.product_description,
        *(fact.text for fact in data.facts),
        *data.must_include,
        *data.must_preserve,
        *data.forbidden,
        *data.assumptions,
    ]
    for candidate in candidates:
        text = _clean_display(candidate, limit=240)
        if text and any(marker in text for marker in _NO_PRODUCT_MARKERS):
            return text
    return None


def _normalize_ready_draft(data: BriefData) -> BriefData:
    if data.product_description and data.product_description.strip():
        return data
    explicit = _no_product_text(data)
    if explicit is None:
        return data
    return data.model_copy(update={"product_description": explicit})


def _is_no_product_text(value: Any) -> bool:
    text = _clean_display(value, limit=300)
    return bool(text) and any(marker in text for marker in _NO_PRODUCT_MARKERS)


def _validate_ready_draft(
    data: BriefData,
    *,
    allowed: set[str],
    size: str,
    reference_asset_id: str | None,
    reference_usage: str,
    has_reference: bool,
    text_authorized: bool,
    message_ids: set[str],
    reference_evidence: str | None,
    reference_texts: dict[str, list[str]],
) -> None:
    if data.size != size:
        raise conflict("SIZE_UNSUPPORTED", templates.render("SIZE_UNSUPPORTED"))
    if not data.product_description or not data.product_description.strip():
        raise _invalid_service()
    if not data.purpose or not data.purpose.strip():
        raise _invalid_service()
    if data.unresolved:
        raise _invalid_service()
    _validate_partial_draft(
        data,
        allowed=allowed,
        size=size,
        reference_asset_id=reference_asset_id,
        reference_usage=reference_usage,
        has_reference=has_reference,
        text_authorized=text_authorized,
        message_ids=message_ids,
        reference_evidence=reference_evidence,
        reference_texts=reference_texts,
    )


_MAX_QUESTION_CHARS = 300
_QUESTION_SUGGESTION_MARKERS = (
    "建议使用",
    "建议采用",
    "推荐使用",
    "可以采用",
    "可以改为",
    "方案：",
    "文案：",
    "标题：",
)
_QUESTION_CONTACT = re.compile(
    r"https?://|www\.|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)",
    re.IGNORECASE,
)


def _fallback_question(data: BriefData | None, *, product_missing: bool = False) -> str:
    product = _clean_display(getattr(data, "product_description", None), limit=180)
    if product_missing or not product:
        return "请说明要展示的商品主体是什么？例如：A 米色托特包；B 黑色托特包。"
    purpose = _clean_display(getattr(data, "purpose", None), limit=180)
    if not purpose:
        return "这张图主要用在什么场景？例如：A 电商商品主图；B 社交平台种草图。"
    return "请补充这张商品图还需要明确的信息，例如画面比例、必须保留的细节或参考图用途。"


def _sanitize_questions(output: RoleOutput, data: BriefData | None) -> list[str]:
    """Make clarification questions safe to display without blocking the turn.

    Clarification is a transport concern, not an approval gate: one malformed
    question must not 409 the whole answer and strand the user. Drop unsafe
    items, clean the rest, cap their size and always leave one answerable
    fallback so the conversation can continue.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    total = 0
    for raw in output.questions:
        if not isinstance(raw, str):
            continue
        if _has_corrupted_text(raw):
            continue
        if any(marker in raw for marker in _QUESTION_SUGGESTION_MARKERS):
            continue
        question = _clean_display(raw, limit=_MAX_QUESTION_CHARS)
        question = _QUESTION_CONTACT.sub("（联系方式已移除）", question).strip()
        if not question or question in seen:
            continue
        remaining = _MAX_QUESTIONS_CHARS - total
        if remaining < 20:
            break
        if len(question) > remaining:
            question = question[:remaining].rstrip()
        if not question:
            break
        cleaned.append(question)
        seen.add(question)
        total += len(question)
        if len(cleaned) == 8:
            break
    if not cleaned:
        product_missing = data is not None and not _clean_display(
            getattr(data, "product_description", None), limit=180
        )
        cleaned = [_fallback_question(data, product_missing=product_missing)]
    return cleaned


def _validated_questions(output: RoleOutput) -> list[str]:
    """Backward-compatible safe questions for persisted clarification replay."""
    return _sanitize_questions(output, None)


def _joined_questions(output: RoleOutput) -> str:
    return "\n".join(_validated_questions(output))


async def server_evidence_ids(
    ctx: WorkflowContext, *, target_id: str, artifact_id: str | None = None
) -> list[str]:
    """Rebuild the evidence set from current rows instead of trusting history."""
    messages = await _user_messages(ctx)
    evidence = [target_id, *(message.id for message in messages)]
    if ctx.project.reference_asset_id and await current_reference_report(ctx) is not None:
        evidence.append(f"{_REFERENCE_EVIDENCE_PREFIX}{ctx.project.reference_asset_id}")
    if artifact_id is not None:
        evidence.append(artifact_id)
    return list(dict.fromkeys(evidence))


def _blocking_slot(rule_id: str, instance: str) -> bool:
    rule = _RULESET.slot((rule_id, instance))
    return rule is not None and rule.kind == "blocking"


def _validate_review(
    output: RoleOutput, *, target_id: str, allowed_evidence: set[str]
) -> tuple[str, list[dict[str, Any]], list[str]]:
    if output.status != "ready" or output.data is None:
        raise _review_invalid(templates.render("REVIEW_INVALID"))
    try:
        adjudication = review_output(
            output.data,
            target_id=target_id,
            ruleset=_RULESET,
            allowed_evidence=allowed_evidence,
        )
    except ProtocolIssue as exc:
        raise _review_invalid(exc.message) from None
    repair_codes = [
        check.rule_id
        for check in adjudication.checks
        if check.outcome in {"fail", "unknown"} and _blocking_slot(check.rule_id, check.instance)
    ]
    return adjudication.decision, adjudication.check_payloads(), repair_codes


async def _run_review(
    ctx: WorkflowContext,
    *,
    target_id: str,
    target_kind: str,
    target: dict[str, Any],
    allowed_evidence: tuple[str, ...],
    attempt: int,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    if (
        target_kind == "requirements_questions"
        or gate_mode_for_target(_RULESET.target_kind) == "off"
    ):
        # Clarification is a transport step, not an approval gate. When the
        # global gate mode is off, brief drafts are also approved locally so a
        # reviewer call can never stall the workflow.
        return "pass", [], []
    try:
        output = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {"reviewed_role": "requirements_consultant", "target_kind": target_kind},
            review_targets=[target],
            required_rule_ids=REVIEW_RULE_IDS,
            allowed_evidence_ids=allowed_evidence,
            attempt=attempt,
        )
    except ApiError:
        raise _review_unavailable() from None
    return _validate_review(
        output, target_id=target_id, allowed_evidence=set(allowed_evidence)
    )


async def _fail_run(ctx: WorkflowContext, template_id: str) -> None:
    await set_stage(
        ctx,
        STAGE,
        "failed",
        issue_code=template_id,
        template_id=template_id,
    )


async def _stop_review(ctx: WorkflowContext) -> None:
    await set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code="REVIEW_REJECTED",
        template_id="REVIEW_REJECTED",
    )


async def _record_review_error(ctx: WorkflowContext, error: ApiError) -> None:
    """Contract defects block the gate; only real outages mark the stage failed."""
    if error.code in {"REVIEW_INVALID", "REVIEW_REJECTED"}:
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code=error.code,
            template_id=error.code,
            message=error.message,
        )
        return
    await _fail_run(ctx, "REVIEW_UNAVAILABLE")


_CORRUPTED_TEXT_MARKERS = ("\ufffd", "锟斤拷", "烫烫烫", "屯屯屯", "Ã", "Â", "â€")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _has_corrupted_text(value: str) -> bool:
    """Reject replacement characters, control bytes and common mojibake."""
    return bool(_CONTROL_CHARS.search(value)) or any(
        marker in value for marker in _CORRUPTED_TEXT_MARKERS
    )


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def _validate_text_hygiene(data: BriefData) -> None:
    if any(_has_corrupted_text(text) for text in _walk_strings(data.model_dump(mode="json"))):
        raise _invalid_service()


def _clean_display(value: Any, *, limit: int = 160) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _CONTROL_CHARS.sub("", text)
    return " ".join(text.split())[:limit]


def _join_display(values: Any, *, limit: int = 3) -> str:
    if not isinstance(values, list):
        return ""
    parts = [
        item
        for value in values[:limit]
        if (item := _clean_display(value, limit=80))
    ]
    return "；".join(parts)


def _alignment_report(
    data: BriefData | None,
    *,
    questions: list[str],
    size: str,
    reference_asset_id: str | None,
    reference_usage: str,
    ready: bool,
    answers: list[dict[str, Any]] | None = None,
    user_text: str = "",
    selected_size: str | None = None,
    pending_update: bool = False,
    resolved_fields: set[str] | None = None,
    unresolved_fields: set[str] | None = None,
    clarifications: list[dict[str, Any]] | None = None,
) -> str:
    """Build the user-facing requirements report from validated fields only."""
    resolved_topics = _resolved_topics(answers)
    if "texts" in (resolved_fields or set()):
        resolved_topics.add("texts")
    if "must_preserve" in (resolved_fields or set()):
        resolved_topics.add("preserve")
    if "texts" in (unresolved_fields or set()):
        resolved_topics.discard("texts")
    if "must_preserve" in (unresolved_fields or set()):
        resolved_topics.discard("preserve")
    text_choice = _explicit_text_choice(user_text)
    if text_choice is False:
        resolved_topics.add("texts")
    elif text_choice is True:
        resolved_topics.discard("texts")
    product = _clean_display(getattr(data, "product_description", None), limit=180)
    purpose = _clean_display(getattr(data, "purpose", None), limit=180)
    subject_label = "画面主体" if _is_no_product_text(product) else "商品主体"
    preserve = _join_display(getattr(data, "must_preserve", []))
    style = _clean_display("、".join(getattr(data, "style_keywords", []) or []), limit=120)
    composition = _clean_display(getattr(data, "composition", None), limit=120)
    lighting = _clean_display(getattr(data, "lighting", None), limit=120)
    visual = "；".join(item for item in (style, composition, lighting) if item)
    texts = _join_display(getattr(data, "texts", []))
    actual_size = _clean_display(getattr(data, "size", None), limit=40)
    default_size = f"服务默认 {size}（尚未由用户确认）"
    if not ready:
        requested = _requested_size(selected_size or user_text)
        actual_size = f"用户期望 {requested[0]}（执行尺寸待确认）" if requested else ""
    usage_labels = {
        "product": "商品参考（保留商品外观）",
        "style": "风格参考（只借视觉风格）",
        "composition": "构图参考（只借画面布局）",
        "none": "无参考图",
    }
    usage = usage_labels.get(reference_usage, reference_usage)
    if reference_asset_id is None:
        usage = "无参考图"

    basic = [
        (subject_label, product),
        ("使用场景与目标", purpose),
        ("输出尺寸", actual_size),
    ]
    filled = sum(1 for _, value in basic if value)
    lines = ["需求对齐情况", f"- 基本信息：{filled}/3 项已明确"]
    if pending_update:
        lines[1] = "- 下列结构化字段沿用上轮记录；本轮补充已保存为原文，待尺寸确认后统一更新。"
    for label, value in basic:
        unknown = "尚未结构化" if data is None else "待补充"
        lines.append(f"- {label}：{value or (default_size if label == '输出尺寸' else unknown)}")
    if not ready and actual_size:
        lines.append(f"- {default_size}")
    if user_text:
        lines.append(f"- 已收到的用户需求（原文）：{_clean_display(user_text, limit=800)}")
    feature_label = "必须保留的画面特征" if subject_label == "画面主体" else "必须保留的商品特征"
    if preserve:
        preserve_display = preserve
    elif "preserve" in resolved_topics:
        preserve_display = "无（已确认）"
    else:
        preserve_display = "未指定（如无必须保留项，可继续）" if ready else ("尚未结构化" if data is None else "待补充")
    visual_display = visual or ("待后续创意与视觉方案补全" if ready else ("尚未结构化" if data is None else "待补充"))
    if texts:
        texts_display = texts
    elif "texts" in resolved_topics:
        texts_display = "无画面文字（已确认）"
    else:
        texts_display = "无画面文字" if ready else ("尚未结构化" if data is None else "待补充")
    lines.append(f"- {feature_label}：{preserve_display}")
    lines.append(f"- 风格、构图与光影：{visual_display}")
    lines.append(f"- 画面文字：{texts_display}")
    lines.append(f"- 参考图用途：{usage}")

    missing: list[str] = []
    if not ready and data is not None and not pending_update:
        if not product:
            missing.append(subject_label)
        if not purpose:
            missing.append("使用场景与目标")
        if not preserve and "preserve" not in resolved_topics:
            missing.append(f"{feature_label}（如没有必须保留项，请直接说明）")
        if not visual:
            missing.append("风格、构图与光影")
        if not texts and "texts" not in resolved_topics:
            missing.append("画面文字（如不需要文字，请直接说明“不需要文字”）")
        if reference_asset_id is not None and reference_usage not in {
            "product",
            "style",
            "composition",
        }:
            missing.append("参考图使用方式")

    lines.append("")
    lines.append("还缺少的信息")
    if missing:
        lines.extend(f"- {item}" for item in missing)
    elif questions:
        lines.append("- 下方澄清问题尚未回答，请补充后再继续。")
    else:
        lines.append("- 无，需求信息已对齐。")

    if questions:
        lines.append("")
        lines.append("请补充以下信息")
        lines.extend(
            f"{index}. {_clean_display(question, limit=300)}"
            for index, question in enumerate(questions, 1)
        )
        lines.append("")
        lines.append("回复方式")
        selectable = {
            index: [option.group(1).upper() for option in _OPTION_LINE.finditer(question)]
            for index, question in enumerate(questions, 1)
        }
        if clarifications is not None:
            by_display = {requirements.render_question(q): q for q in clarifications}
            selectable = {
                index: [option["id"] for option in by_display.get(question, {}).get("options", [])]
                for index, question in enumerate(questions, 1)
            }
        example = " ".join(
            f"{index}{options[0]}" for index, options in selectable.items() if options
        )
        placeholders = " ".join(f"{index}?" for index in range(1, len(questions) + 1))
        if example:
            lines.append(f"- 直接回复选项编号即可，例如「{example}」；也可以按编号补充自己的描述。")
        else:
            lines.append("- 请按编号用自然语言回答，例如「1. 黑色磨砂笔身，银色笔夹」。")
        lines.append("- 请按题目编号逐条回复；未回复的题会继续保留。")
        lines.append("- 回答过的问题不会重复询问，信息足够后会继续下一步。")
        lines.append(f"- 可以复制这一行开始：{placeholders}（把 ? 换成你的选择或简短说明）")
    elif ready:
        lines.append("")
        lines.append("下一步：确认需求后将生成图片提示词；只有你确认提示词后，系统才会生成图片。")
    return "\n".join(lines)


async def _append_assistant(
    ctx: WorkflowContext, content: str, revision: int
) -> Message | None:
    """Persist one workflow assistant message, idempotently for a revision."""
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        existing = await session.scalar(
            select(Message)
            .where(
                Message.project_id == ctx.project_id,
                Message.role == "assistant",
                Message.turn_kind == "workflow",
                Message.project_revision == project.revision,
            )
            .order_by(Message.sequence_no.desc())
            .limit(1)
        )
        if existing is not None and existing.content == content:
            return existing
        highest = await session.scalar(
            select(func.max(Message.sequence_no)).where(
                Message.project_id == ctx.project_id
            )
        )
        message = Message(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest or 0) + 1,
            role="assistant",
            content=content,
            project_revision=project.revision,
            brief_snapshot_json=None,
            operation_id=None,
            turn_kind="workflow",
            created_at=now,
        )
        session.add(message)
        await session.flush()
        return message


def _blocked_template(output: RoleOutput) -> tuple[str, str]:
    codes = {
        str(issue.get("code")) for issue in output.issues if isinstance(issue, dict)
    }
    if "UNSAFE_REQUEST" in codes:
        return "UNSAFE_REQUEST", "SAFETY_REFUSAL"
    if codes & {"OUT_OF_SCOPE", "MIXED_SCOPE"}:
        return "OUT_OF_SCOPE", "SCOPE_REFUSAL"
    return "MISSING_EVIDENCE", "SERVICE_UNAVAILABLE"


async def _previous_partial(ctx: WorkflowContext, reference_usage: str) -> dict[str, Any] | None:
    """Read only server-validated clarification data for this run/reference."""
    persisted = requirements.current_data(ctx, reference_usage)
    if persisted is not None:
        return persisted
    async with read_session(ctx.session_factory) as session:
        events = list((await session.scalars(
            select(DiagnosticEvent).where(
                DiagnosticEvent.project_id == ctx.project_id,
                DiagnosticEvent.event == "requirements_partial_validated",
            ).order_by(DiagnosticEvent.created_at.desc()).limit(64)
        )).all())
    recovered: dict[str, Any] | None = None
    for event in events:
        detail = _json_dict(event.detail_json)
        if (detail.get("run_id") == ctx.run_id
            and detail.get("reference_asset_id") == ctx.project.reference_asset_id
            and isinstance(detail.get("data"), dict)
            and detail["data"].get("reference_usage") == reference_usage):
            try:
                validated = BriefData.model_validate(detail["data"]).model_dump(mode="json")
            except ValueError:
                continue
            if recovered is None:
                recovered = validated
            else:
                # Repair old runs that already lost a known field before this
                # migration. Only fill gaps, never override a later known value.
                for field in ("product_description", "purpose", "style_keywords", "composition", "lighting"):
                    if recovered.get(field) in (None, "", []) and validated.get(field) not in (None, "", []):
                        recovered[field] = validated[field]
    return recovered


async def _merge_requirement_output(
    ctx: WorkflowContext, current: dict[str, Any] | None, output: RoleOutput,
    messages: list[Message], answers: list[dict[str, Any]],
) -> tuple[BriefData, dict[str, Any]]:
    try:
        return requirements.merge(
            current, output.data or {}, updates=output.requirement_updates,
            evidence=_requirement_evidence(messages, answers), strict=ctx.consolidated,
            prior_sources=requirements.state_of(ctx).get("field_sources"),
        )
    except ApiError as exc:
        from app.workflow.consolidated import checkpoint_after_validation

        await checkpoint_after_validation(ctx, output)
        await _fail_run(ctx, "LLM_RESPONSE_INVALID")
        raise ApiError(409, "LLM_RESPONSE_INVALID", templates.render("LLM_RESPONSE_INVALID")) from exc


async def _compose(
    ctx: WorkflowContext,
    action: str,
    text: str | None,
    *,
    record_user_text: bool = True,
    current_requirement: dict[str, Any] | None = None,
) -> None:
    if record_user_text:
        if action != "compose_brief":
            if text is None:
                raise validation_error("消息内容不能为空")
            await _record_user_text(ctx, text)
            await _supersede_prior_draft(ctx)
        elif text is not None:
            await _record_user_text(ctx, text)
            await _supersede_prior_draft(ctx)

    size = _configured_size(ctx)
    await _sync_requirement_revision(ctx)
    await set_stage(ctx, STAGE, "active")

    messages = await _user_messages(ctx)
    if not messages:
        raise conflict("MISSING_INPUT", "尚未收到用户需求描述")
    pending_clarification = await _pending_clarification(ctx)
    clarification_answers = await _clarification_history(ctx)
    conversation = await _conversation_messages(ctx)

    reference_pair = await current_reference_report(ctx)
    reference_asset_id = ctx.project.reference_asset_id
    if reference_asset_id and reference_pair is None:
        # A present image without an approved report must never silently
        # downgrade to the text-only path.
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="REFERENCE_REPORT_NOT_APPROVED",
            template_id="ACTION_UNAVAILABLE",
        )
        raise conflict(
            "REFERENCE_REPORT_NOT_APPROVED",
            "参考图报告尚未通过独立审查，不能进入需求整理",
        )
    reference = reference_pair[0] if reference_pair is not None else None
    reference_payload = reference_pair[1] if reference_pair is not None else None
    expected_usage = (
        str(reference_payload.get("reference_usage"))
        if reference_payload is not None
        else "none"
    )
    reference_evidence = (
        f"{_REFERENCE_EVIDENCE_PREFIX}{reference_asset_id}"
        if reference_payload is not None
        else None
    )
    reference_texts = (
        _reference_evidence_texts(reference_payload)
        if reference_payload is not None
        else {}
    )
    approved_inputs: list[dict[str, Any]] = (
        [_reference_approved_input(reference, reference_payload, reference_asset_id)]
        if reference is not None and reference_payload is not None
        else []
    )
    evidence = tuple(message.id for message in messages) + (
        (reference_evidence,) if reference_evidence is not None else ()
    )
    previous_final = await latest_project_artifact(
        ctx, "final_brief", status="approved"
    )
    if previous_final is not None and previous_final.run_id != ctx.run_id:
        previous_payload = _json_dict(previous_final.payload_json)
        approved_inputs.append(
            {
                "kind": "final_brief",
                "artifact_id": previous_final.id,
                "digest": previous_final.digest,
                "payload": previous_payload,
            }
        )
        evidence = (*evidence, previous_final.id)
        if current_requirement is None:
            current_requirement = previous_payload
    message_ids = {message.id for message in messages}
    text_authorized = _text_authorized(messages)
    user_text = "\n\n".join(message.content for message in messages)
    configured_dimensions = _requested_size(size)
    requested_size: tuple[str, float] | None = None
    original_size: tuple[str, float] | None = None
    size_paused = False
    # Replay decisions in message order: a bound code choice survives unrelated
    # follow-up answers, while a later explicit size amendment replaces it.
    for message in messages:
        explicit = _requested_size(message.content)
        if explicit:
            requested_size = explicit
            original_size = original_size or explicit
            size_paused = False
        elif (
            configured_dimensions and configured_dimensions[1] > 1
            and re.search(r"(?:接受|采用|改为|改成|使用)[^。\n]{0,20}(?:横图|横版)", message.content)
            and not any(word in message.content for word in ("不接受", "不要", "不采用", "不改"))
        ):
            requested_size = configured_dimensions
            size_paused = False
        for answer in clarification_answers:
            if answer.get("reply_message_id") != message.id or "size" not in answer.get("topics", []):
                continue
            if "保留原比例" in answer["answer"]:
                requested_size = original_size
                size_paused = True
            elif selected := _requested_size(answer["answer"]):
                requested_size = selected
                size_paused = False
    requested_conflict = size_paused or bool(
        requested_size and configured_dimensions
        and abs(requested_size[1] - configured_dimensions[1]) > 0.001
    )
    capabilities = ctx.registry.visual_capabilities(ctx.settings.visual().adapter_id or "")
    executable_sizes = [size] if capabilities is None or capabilities.supported_image_sizes is None or size in capabilities.supported_image_sizes else []
    if not executable_sizes:
        await _fail_run(ctx, "CAPABILITY_NOT_READY")
        raise ApiError(503, "CAPABILITY_NOT_READY", templates.render("CAPABILITY_NOT_READY", capability_label="图片生成"))
    conflict_hints = _reference_conflict_hints(messages, reference_payload)

    previous = await latest_artifact(ctx, "brief_draft")
    previous_payload = _json_dict(previous.payload_json) if previous is not None else {}
    if previous is not None and (
        previous_payload.get("reference_asset_id") != reference_asset_id
        or previous_payload.get("reference_usage") != expected_usage
    ):
        # A changed image or usage creates a new requirement version. The old
        # draft and every downstream approval must not survive it.
        await set_artifact_status(ctx, previous.id, "superseded")
        await revoke_downstream_approvals(ctx, "reference_changed")
        await record_diagnostic(
            ctx,
            "reference_requirement_changed",
            {
                "artifact_id": previous.id,
                "reference_asset_id": reference_asset_id,
                "reference_usage": expected_usage,
            },
        )

    persisted_requirement = requirements.current_data(ctx, expected_usage)
    if persisted_requirement is not None:
        current_requirement = persisted_requirement
    if current_requirement is None:
        current_brief = await approved_artifact(ctx, "brief_draft")
        current_requirement = (
            _json_dict(current_brief.payload_json)
            if current_brief is not None
            else None
        )

    previous_partial = await _previous_partial(ctx, expected_usage)
    if current_requirement is None and previous_partial is not None:
        current_requirement = previous_partial

    if not ctx.direct and requested_conflict and pending_clarification and "当前输出仅支持" in pending_clarification:
        # Capability negotiation is deterministic. Retain follow-up details and
        # the last validated partial without another model call for the same
        # unsupported ratio. The accepted size still requires fresh composition.
        requested = requested_size[0] if requested_size else "原比例"
        report = _alignment_report(
            BriefData.model_validate(previous_partial) if previous_partial else None,
            questions=[_size_question(size, requested)], size=size,
            reference_asset_id=reference_asset_id, reference_usage=expected_usage,
            ready=False, answers=clarification_answers, user_text=user_text,
            selected_size=requested, pending_update=True,
        )
        report += "\n\n本轮补充已保存；明确接受可执行尺寸后，会结合全部回答重新整理需求。"
        await _append_assistant(ctx, report, ctx.project.revision)
        await set_stage(ctx, STAGE, "needs_input", issue_code="SIZE_UNSUPPORTED", template_id="SIZE_UNSUPPORTED")
        return

    latest = await latest_artifact(ctx, "brief_draft")
    attempt = (latest.revision_no if latest is not None else 0) + 1
    if attempt > _MAX_CANDIDATES and not ctx.direct:
        raise conflict(
            "REVISION_BUDGET_EXHAUSTED",
            "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
        )

    repair_codes: list[str] = []
    while True:
        try:
            from app.workflow.consolidated import run_requirements

            output = await run_requirements(
                ctx,
                "requirements_consultant",
                STAGE,
                {
                    "instruction": action,
                    "user_text": user_text,
                    "user_messages": [
                        {"message_id": message.id, "text": message.content}
                        for message in messages
                    ],
                    "conversation": conversation,
                    "pending_clarification": pending_clarification,
                    "clarification_answers": clarification_answers,
                    "configured_size": size,
                    "executable_sizes": executable_sizes,
                    "requested_size": requested_size[0] if requested_size else None,
                    "size_requires_explicit_acceptance": requested_conflict,
                    "reference_asset_id": reference_asset_id,
                    "reference_usage": expected_usage,
                    "reference_report": reference_payload,
                    "reference_uncertainties": (
                        reference_payload.get("uncertainties")
                        if reference_payload
                        else []
                    ),
                    "reference_conflict_hints": conflict_hints,
                    "current_requirement": current_requirement,
                },
                approved_inputs=approved_inputs,
                repair_codes=repair_codes,
                required_rule_ids=REVIEW_RULE_IDS,
                allowed_evidence_ids=evidence,
                attempt=attempt,
            )
        except ApiError as exc:
            await _fail_run(
                ctx,
                exc.code if exc.code in {"CAPABILITY_NOT_READY", "LLM_TIMEOUT", "LLM_RESPONSE_INVALID"}
                else "SERVICE_UNAVAILABLE",
            )
            raise

        if output is None:
            return
        if ctx.direct:
            from app.workflow.collaboration import accept_requirements

            await accept_requirements(
                ctx, output, current_requirement, messages,
                size_conflict=requested_conflict, requested_size=requested_size,
                reference_usage=expected_usage,
            )
            return
        if not ctx.consolidated and output.data is not None:
            # Historical consultant envelopes have no delta field. Preserve
            # explicit user cancellations while applying the same accumulator.
            latest_text = _requirement_evidence(messages, clarification_answers)[messages[-1].id]
            updates = list(output.requirement_updates)
            if _explicit_text_choice(latest_text) is False:
                updates.append({"field": "texts", "operation": "clear", "value": None,
                                "evidence_ids": [messages[-1].id]})
            if any(phrase in latest_text for phrase in ("没有必须保留", "无必须保留", "无需保留", "不需要保留", "没有特殊保留")):
                updates.append({"field": "must_preserve", "operation": "clear", "value": None,
                                "evidence_ids": [messages[-1].id]})
            output = replace(output, requirement_updates=updates)
        size_conflict = output.status in {"ready", "needs_input"} and (
            requested_conflict
            or (output.data is not None and output.data.get("size") not in {None, size})
        )
        if size_conflict:
            requested = requested_size[0] if requested_size else str(output.data.get("size"))
            output = replace(output, status="needs_input", questions=[_size_question(size, requested)])

        if output.status in {"needs_input", "blocked"}:
            partial_data: BriefData | None = None
            if output.data is not None:
                partial_data, field_sources = await _merge_requirement_output(
                    ctx, current_requirement, output, messages, clarification_answers,
                )
                _validate_partial_draft(
                    partial_data,
                    allowed=set(evidence),
                    size=size,
                    reference_asset_id=reference_asset_id,
                    reference_usage=expected_usage,
                    has_reference=reference is not None,
                    text_authorized=text_authorized,
                    message_ids=message_ids,
                    reference_evidence=reference_evidence,
                    reference_texts=reference_texts,
                )
            for issue in output.issues:
                if isinstance(issue, dict):
                    issue_evidence = issue.get("evidence_ids") or []
                    if not all(
                        isinstance(item, str) and item in evidence
                        for item in issue_evidence
                    ):
                        await _fail_run(ctx, "SERVICE_UNAVAILABLE")
                        raise _invalid_service()
            target_id = str(uuid4())
            target = {
                "target_id": target_id,
                "target_kind": "requirements_questions",
                "status": output.status,
                "data": output.data,
                "issues": output.issues,
                "questions": output.questions,
                "user_text": user_text,
                "configured_size": size,
                "reference_asset_id": reference_asset_id,
                "reference_usage": expected_usage,
            }
            try:
                decision, checks, repair_codes = await _run_review(
                    ctx,
                    target_id=target_id,
                    target_kind="requirements_questions",
                    target=target,
                    allowed_evidence=(target_id, *evidence),
                    attempt=attempt,
                )
            except ApiError as exc:
                await _record_review_error(ctx, exc)
                raise
            if decision == "pass":
                if output.status == "needs_input":
                    try:
                        if ctx.consolidated and partial_data is not None and output.clarifications:
                            remaining = []
                            for question in output.clarifications:
                                field = question["field"]
                                source = field_sources.get(field, {})
                                known = getattr(partial_data, field) not in (None, "", [])
                                if source.get("status") in {"unknown", "conflict"} or field in partial_data.unresolved or not known and source.get("status") != "resolved":
                                    remaining.append(question)
                            output = replace(output, clarifications=remaining,
                                             questions=[requirements.render_question(q) for q in remaining])
                        if size_conflict:
                            questions = output.questions
                        elif ctx.consolidated:
                            questions = _sanitize_questions(output, partial_data)
                        else:
                            questions = _drop_resolved_questions(
                                _sanitize_questions(output, partial_data), clarification_answers, user_text,
                            )
                        if not questions:
                            questions = [
                                "请确认以上信息是否准确；如需调整，请直接说明要修改哪一项。"
                            ]
                        question_text = "\n".join(questions)
                        report = _alignment_report(
                            partial_data,
                            questions=questions,
                            size=size,
                            reference_asset_id=reference_asset_id,
                            reference_usage=expected_usage,
                            ready=False,
                            answers=clarification_answers,
                            user_text=user_text,
                            resolved_fields={key for key, value in field_sources.items() if value.get("status") == "resolved"} if partial_data is not None else set(),
                            unresolved_fields={key for key, value in field_sources.items() if value.get("status") in {"unknown", "conflict"}} if partial_data is not None else set(),
                            clarifications=output.clarifications if ctx.consolidated and not size_conflict else None,
                            selected_size=requested_size[0] if requested_size else None,
                        )
                    except ApiError as exc:
                        await _record_review_error(ctx, exc)
                        raise
                    if partial_data is not None:
                        await requirements.save(
                            ctx, partial_data, sources=field_sources,
                            questions=[q for q in output.clarifications if requirements.render_question(q) in questions],
                            answers=clarification_answers,
                        )
                        await record_diagnostic(ctx, "requirements_partial_validated", {
                            "run_id": ctx.run_id,
                            "reference_asset_id": reference_asset_id,
                            "requirement_revision": ctx.run.requirement_revision,
                            "data": partial_data.model_dump(mode="json"),
                        })
                    elif previous_partial is not None:
                        report += "\n\n上轮已核实的信息（本轮修改尚未结构化，请结合本轮原文确认）：\n" + _alignment_report(
                            BriefData.model_validate(previous_partial), questions=[], size=size,
                            reference_asset_id=reference_asset_id, reference_usage=expected_usage,
                            ready=False, answers=clarification_answers,
                        )
                    await _append_assistant(ctx, report, ctx.project.revision)
                    await set_stage(
                        ctx,
                        STAGE,
                        "needs_input",
                        issue_code="SIZE_UNSUPPORTED" if size_conflict else "MISSING_INPUT",
                        template_id="SIZE_UNSUPPORTED" if size_conflict else None,
                        message=question_text,
                    )
                else:
                    code, template_id = _blocked_template(output)
                    await set_stage(
                        ctx,
                        STAGE,
                        "blocked",
                        issue_code=code,
                        template_id=template_id,
                    )
                return
            if decision == "revise" and attempt < _MAX_CANDIDATES:
                attempt += 1
                continue
            await _stop_review(ctx)
            return

        if output.status != "ready" or output.data is None:
            await _fail_run(ctx, "SERVICE_UNAVAILABLE")
            raise _invalid_service()

        merged, field_sources = await _merge_requirement_output(
            ctx, current_requirement, output, messages, clarification_answers,
        )
        data = _normalize_ready_draft(merged)
        try:
            _validate_ready_draft(
                data,
                allowed=set(evidence),
                size=size,
                reference_asset_id=reference_asset_id,
                reference_usage=expected_usage,
                has_reference=reference is not None,
                text_authorized=text_authorized,
                message_ids=message_ids,
                reference_evidence=reference_evidence,
                reference_texts=reference_texts,
            )
        except ApiError:
            await _fail_run(ctx, "SERVICE_UNAVAILABLE")
            raise

        if conflict_hints:
            # The role must not turn one side of an obvious color/quantity
            # conflict into a confirmed fact. A ready draft is therefore
            # stopped before it becomes a candidate.
            await set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code="CONFLICTING_INPUT",
                template_id="ACTION_UNAVAILABLE",
            )
            raise conflict(
                "CONFLICTING_INPUT",
                "用户描述与参考图观察存在冲突，请先集中确认",
            )

        try:
            artifact = await start_artifact(ctx, "brief_draft", data)
        except ApiError as exc:
            if exc.code == "REVISION_BUDGET_EXHAUSTED":
                await _stop_review(ctx)
                return
            raise

        target = {
            "target_id": artifact.id,
            "target_kind": "brief_draft",
            "status": output.status,
            "data": data.model_dump(mode="json"),
            "issues": output.issues,
            "questions": output.questions,
            "user_text": user_text,
            "configured_size": size,
            "reference_asset_id": reference_asset_id,
            "reference_usage": expected_usage,
        }
        try:
            decision, checks, repair_codes = await _run_review(
                ctx,
                target_id=artifact.id,
                target_kind="brief_draft",
                target=target,
                allowed_evidence=(artifact.id, *evidence),
                attempt=attempt,
            )
        except ApiError as exc:
            await _record_review_error(ctx, exc)
            raise

        await add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision=decision,
            checks=checks,
            raw={
                "results": [
                    {"target_id": artifact.id, "decision": decision, "checks": checks}
                ]
            },
        )

        if decision == "pass":
            await requirements.save(
                ctx, data, sources=field_sources, questions=[], answers=clarification_answers,
            )
            await record_diagnostic(
                ctx,
                "requirements_brief_approved",
                {
                    "artifact_id": artifact.id,
                    "artifact_digest": artifact.digest,
                    "reference_artifact_id": reference.id
                    if reference is not None
                    else None,
                    "reference_digest": reference.digest
                    if reference is not None
                    else None,
                    "reference_asset_id": reference_asset_id,
                    "reference_usage": expected_usage,
                },
            )
            await _append_assistant(
                ctx,
                _alignment_report(
                    data,
                    questions=[],
                    size=size,
                    reference_asset_id=reference_asset_id,
                    reference_usage=expected_usage,
                    ready=True,
                ),
                ctx.project.revision,
            )
            await set_stage(ctx, "S04", "active")
            return
        if decision == "revise" and artifact.revision_no < _MAX_CANDIDATES:
            await set_artifact_status(ctx, artifact.id, "superseded")
            attempt = artifact.revision_no + 1
            continue
        if decision == "unknown":
            await set_artifact_status(ctx, artifact.id, "rejected")
        await _stop_review(ctx)
        return


async def record_revision_text(ctx: WorkflowContext, text: str) -> Message:
    cleaned = text.strip()
    if not cleaned:
        raise validation_error("消息内容不能为空")
    if len(text) > 20_000:
        raise validation_error("消息内容过长")
    return await _record_user_text(ctx, text)


async def start_requirements_revision(
    ctx: WorkflowContext,
    text: str,
    *,
    current_requirement: dict[str, Any] | None = None,
    record_user_text: bool = True,
) -> None:
    """Record an explicit requirements edit and rerun the S03 review chain.

    The caller owns the S06 confirmation checks. This helper only persists the
    exact user text, revokes downstream approvals and starts a fresh S03 draft;
    it never edits the final brief or reuses the previous approval.
    """
    cleaned = text.strip()
    if not cleaned:
        raise validation_error("消息内容不能为空")
    if len(text) > 20_000:
        raise validation_error("消息内容过长")
    if record_user_text:
        await _record_user_text(ctx, text)
    await _supersede_prior_draft(ctx)
    await _compose(
        ctx,
        "revise_requirements",
        None,
        record_user_text=False,
        current_requirement=current_requirement,
    )


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    text = (
        _optional_text(payload) if action == "compose_brief" else _require_text(payload)
    )
    await _compose(ctx, action, text)


__all__ = [
    "ACTIONS",
    "REVIEW_RULE_IDS",
    "STAGE",
    "available",
    "handle",
    "start_requirements_revision",
]
