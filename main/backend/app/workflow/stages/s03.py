from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Message, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
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
    record_diagnostic,
    revoke_downstream_approvals,
    set_artifact_status,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, WorkflowRun

STAGE = "S03"
ACTIONS = [
    "compose_brief",
    "answer_requirements_clarification",
    "revise_brief_draft",
]

# The independent reviewer attests every applicable G03 rule. A pass with a
# missing, duplicated, extra, failed or unknown check never releases a draft.
REVIEW_RULE_IDS = (
    "G03_STRUCTURE",
    "G03_SUBJECT_PURPOSE",
    "G03_SOURCE_BASIS",
    "G03_TEXTS",
    "G03_CONSTRAINTS",
    "G03_SIZE",
    "G03_UNRESOLVED",
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
                    )
                    .order_by(Message.sequence_no.asc())
                )
            ).all()
        )


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


def _text_authorized(messages: list[Message]) -> bool:
    text = "\n".join(message.content for message in messages)
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
    if data.size != size:
        raise _invalid_service()
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


def _joined_questions(output: RoleOutput) -> str:
    questions = [question.strip() for question in output.questions if question.strip()]
    if not questions:
        raise _invalid_service()
    text = "\n".join(questions)
    if len(text) > _MAX_QUESTIONS_CHARS:
        raise _invalid_service()
    return text


def _validate_review(
    output: RoleOutput, *, target_id: str, allowed_evidence: set[str]
) -> tuple[str, list[dict[str, Any]], list[str]]:
    if output.status != "ready" or output.data is None:
        raise _review_unavailable()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise _review_unavailable()
    result = results[0]
    if not isinstance(result, dict) or result.get("target_id") != target_id:
        raise _review_unavailable()
    decision = result.get("decision")
    if decision not in {"pass", "revise", "reject", "unknown"}:
        raise _review_unavailable()
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _review_unavailable()
    seen: list[str] = []
    repair_codes: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            raise _review_unavailable()
        rule_id = check.get("rule_id")
        outcome = check.get("outcome")
        if rule_id not in REVIEW_RULE_IDS:
            raise _review_unavailable()
        if outcome not in {"pass", "fail", "unknown"}:
            raise _review_unavailable()
        evidence = check.get("evidence_ids")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(
                isinstance(item, str) and item in allowed_evidence for item in evidence
            )
        ):
            raise _review_unavailable()
        note = check.get("note")
        if isinstance(note, str) and "://" in note.lower():
            raise _review_unavailable()
        seen.append(str(rule_id))
        if outcome != "pass":
            repair_codes.append(str(rule_id))
    if set(seen) != set(REVIEW_RULE_IDS) or len(seen) != len(set(seen)):
        raise _review_unavailable()
    if decision == "pass" and repair_codes:
        raise _review_unavailable()
    if decision != "pass" and not repair_codes:
        raise _review_unavailable()
    return str(decision), checks, repair_codes


async def _run_review(
    ctx: WorkflowContext,
    *,
    target_id: str,
    target_kind: str,
    target: dict[str, Any],
    allowed_evidence: tuple[str, ...],
    attempt: int,
) -> tuple[str, list[dict[str, Any]], list[str]]:
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
        template_id="REVIEW_UNAVAILABLE",
    )


def _blocked_template(output: RoleOutput) -> tuple[str, str]:
    codes = {
        str(issue.get("code")) for issue in output.issues if isinstance(issue, dict)
    }
    if "UNSAFE_REQUEST" in codes:
        return "UNSAFE_REQUEST", "SAFETY_REFUSAL"
    if codes & {"OUT_OF_SCOPE", "MIXED_SCOPE"}:
        return "OUT_OF_SCOPE", "SCOPE_REFUSAL"
    return "MISSING_EVIDENCE", "SERVICE_UNAVAILABLE"


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
    message_ids = {message.id for message in messages}
    text_authorized = _text_authorized(messages)
    user_text = "\n\n".join(message.content for message in messages)
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

    if current_requirement is None:
        current_brief = await approved_artifact(ctx, "brief_draft")
        current_requirement = (
            _json_dict(current_brief.payload_json)
            if current_brief is not None
            else None
        )

    latest = await latest_artifact(ctx, "brief_draft")
    attempt = (latest.revision_no if latest is not None else 0) + 1
    if attempt > _MAX_CANDIDATES:
        raise conflict(
            "REVISION_BUDGET_EXHAUSTED",
            "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
        )

    repair_codes: list[str] = []
    while True:
        try:
            output = await run_role(
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
                    "configured_size": size,
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
        except ApiError:
            await _fail_run(ctx, "SERVICE_UNAVAILABLE")
            raise

        if output.status in {"needs_input", "blocked"}:
            if output.data is not None:
                _validate_partial_draft(
                    BriefData.model_validate(output.data),
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
            except ApiError:
                await _fail_run(ctx, "REVIEW_UNAVAILABLE")
                raise
            if decision == "pass":
                if output.status == "needs_input":
                    question_text = _joined_questions(output)
                    await set_stage(
                        ctx,
                        STAGE,
                        "needs_input",
                        issue_code="MISSING_INPUT",
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

        data = BriefData.model_validate(output.data)
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
                target_id=artifact.id,
                target_kind="brief_draft",
                target=target,
                allowed_evidence=(artifact.id, *evidence),
                attempt=attempt,
            )
        except ApiError:
            await _fail_run(ctx, "REVIEW_UNAVAILABLE")
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


async def start_requirements_revision(
    ctx: WorkflowContext,
    text: str,
    *,
    current_requirement: dict[str, Any] | None = None,
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
