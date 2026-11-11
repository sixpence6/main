"""Three-call model workflow, retaining the existing approval and artifact seams."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from app.errors import ApiError
from app.workflow import requirements, templates
from app.workflow.contracts import ConversationData, RequirementsAssistantData, VisualPlannerData
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    approved_artifact,
    latest_artifact,
    set_intent,
    set_stage,
    start_artifact,
)


def _bind_current_message(value: Any, message_id: str) -> Any:
    if isinstance(value, dict):
        return {
            key: [message_id if source == "current_user_message" else source for source in item]
            if key == "evidence_ids" and isinstance(item, list)
            else _bind_current_message(item, message_id)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_bind_current_message(item, message_id) for item in value]
    return value


async def run_requirements(
    ctx: WorkflowContext,
    legacy_role: str,
    stage: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> RoleOutput | None:
    if not ctx.consolidated:
        return await run_role(ctx, legacy_role, stage, input_data, **kwargs)
    from app.workflow.stages import s03

    prepared = ctx.prepared_requirements
    ctx.prepared_requirements = None
    if prepared is not None:
        text, output = prepared
        messages = await s03._user_messages(ctx)
        message = next((item for item in reversed(messages) if item.content == text), None)
        if message is None:
            raise ApiError(409, "REQUIREMENT_SOURCE_INVALID", "本轮需求尚未保存")
        output = replace(output, data=_bind_current_message(output.data, message.id))
    else:
        stored = requirements.state_of(ctx)
        output = await run_role(
            ctx,
            "requirements_assistant",
            stage,
            {
                **input_data,
                "field_sources": stored.get("field_sources", {}),
                "structured_clarifications": stored.get("questions", []),
                "pending_feedback": stored.get("pending_feedback", []),
                "conversation_decision": {key: stored.get(key) for key in ("generation_intent", "exploration_rounds", "answered_topics")},
            },
            **kwargs,
        )
    if output.data is None:
        return replace(output, role="requirements_consultant")
    data = RequirementsAssistantData.model_validate(output.data)
    if data.scope in {"out_of_scope", "mixed", "unclear"}:
        template = "SCOPE_REFUSAL" if data.scope != "unclear" else "INTENT_UNCLEAR"
        await set_intent(ctx, data.scope, "none")
        await s03._append_assistant(ctx, templates.render(template), ctx.project.revision)
        await set_stage(
            ctx,
            "S01",
            "needs_input" if data.scope == "unclear" else "blocked",
            issue_code="MISSING_INPUT" if data.scope == "unclear" else "OUT_OF_SCOPE",
            template_id=template,
        )
        return None
    if data.kind == "conversation" and data.brief is None:
        await s03._append_assistant(ctx, data.reply or templates.render("INTENT_UNCLEAR"), ctx.project.revision)
        await set_stage(ctx, "S03", "needs_input")
        return None
    if data.brief is None:
        raise ApiError(409, "LLM_RESPONSE_INVALID", "需求助手没有返回需求数据")
    if output.document is not None:
        from app.workflow.markdown_handoff import save_document

        await save_document(ctx, "requirements_assistant", output.document, output.document_sources)
    await set_intent(ctx, "image_request", "new_request")
    questions = [item.model_dump(mode="json") for item in data.clarifications]
    updates = [item.model_dump(mode="json") for item in data.updates]
    brief = data.brief.model_dump(mode="json")
    status, issues = output.status, list(output.issues)
    stored = requirements.state_of(ctx)
    messages = await s03._user_messages(ctx)
    if messages and re.fullmatch(r"(?:\s*\d+\s*[A-Za-z]\s*)+|\s*[A-Za-z]\s*", messages[-1].content):
        unfinished = [
            answer
            for answer in requirements.parse_answers(
                stored.get("questions", []), messages[-1].content
            )
            if answer.get("requires_detail")
        ]
        for answer in unfinished:
            field = answer["field"]
            previous = (stored.get("data") or {}).get(field)
            brief[field] = (
                previous
                if previous is not None
                else ([] if isinstance(brief.get(field), list) else None)
            )
            updates = [update for update in updates if update["field"] != field]
            updates.append(
                {
                    "field": field,
                    "operation": "unknown",
                    "value": None,
                    "evidence_ids": [messages[-1].id],
                }
            )
            questions = [question for question in questions if question["field"] != field]
            questions.append(
                {
                    "id": answer["question_id"][:150] + "-detail",
                    "field": field,
                    "text": "请提供希望出现在画面中的文字原文。"
                    if ctx.direct and field == "texts"
                    else answer["question"][:240] + " 请直接写出具体内容。",
                    "options": [],
                }
            )
            issues.append(
                {
                    "code": "MISSING_INPUT",
                    "path": field,
                    "detail": "所选项目仍需补充具体内容",
                    "evidence_ids": [messages[-1].id],
                }
            )
        if unfinished:
            status = "needs_input"
    return replace(
        output,
        role="requirements_consultant",
        data=brief,
        status=status,
        issues=issues,
        questions=[requirements.render_question(item) for item in questions],
        requirement_updates=updates,
        clarifications=questions,
        reply=data.reply,
        conversation_only=data.kind == "conversation",
    )


async def checkpoint_after_validation(ctx: WorkflowContext, output: RoleOutput) -> None:
    """Bind a failed, already completed assistant call to its recorded user turn.

    A revision is classified before its message is committed. Its exact model
    request remains unchanged, while recovery belongs to the resulting S03
    revision, not the old stage at which the message arrived.
    """
    if not ctx.consolidated or output.role_call_id is None:
        return
    from app.db import read_session
    from app.workflow.roles import _record_recovery_checkpoint
    from app.workflow.tables import RoleCall

    async with read_session(ctx.session_factory) as session:
        call = await session.get(RoleCall, output.role_call_id)
    if (
        call is None
        or call.run_id != ctx.run_id
        or call.project_id != ctx.project_id
        or call.role != "requirements_assistant"
    ):
        return
    await _record_recovery_checkpoint(
        ctx,
        role=call.role,
        stage="S03",
        capability=call.capability,
        attempt=call.attempt,
        status="completed",
        request_digest=call.request_digest,
        request_json=call.request_json,
        error_code="LLM_RESPONSE_INVALID",
        role_call_id=call.id,
        operation_id="validated:" + call.id,
    )


async def classify_message(
    ctx: WorkflowContext, content: str, has_reference_attachment: bool
) -> ConversationData:
    from app.workflow.stages import s03

    stored = requirements.state_of(ctx)
    messages = await s03._user_messages(ctx)
    report = await s03.current_reference_report(ctx)
    current = stored.get("data")
    if current is None:
        artifact = await approved_artifact(ctx, "brief_draft")
        current = s03._artifact_payload(artifact) if artifact is not None else None
    payload = {
        "user_text": content,
        "pending_user_message": content,
        "current_stage": ctx.run.stage,
        "current_status": ctx.run.status,
        "current_requirement": current,
        "field_sources": stored.get("field_sources", {}),
        "structured_clarifications": stored.get("questions", []),
        "conversation": [
            *(await s03._conversation_messages(ctx)),
            {"role": "user", "text": content},
        ],
        "user_messages": [{"message_id": item.id, "text": item.content} for item in messages]
        + [{"message_id": "current_user_message", "text": content}],
        "pending_feedback": stored.get("pending_feedback", []),
        "pending_clarification": await s03._pending_clarification(ctx),
        "clarification_answers": await s03._clarification_history(ctx),
        "configured_size": s03._configured_size(ctx),
        "executable_sizes": [s03._configured_size(ctx)],
        "reference_asset_id": ctx.project.reference_asset_id,
        "reference_report": report[1] if report is not None else None,
        "has_reference_attachment": has_reference_attachment,
    }
    output = await run_role(
        ctx,
        "requirements_assistant",
        ctx.run.stage,
        payload,
        allowed_evidence_ids=tuple([item.id for item in messages] + ["current_user_message"]),
    )
    if output.data is None:
        raise ApiError(409, "MESSAGE_NOT_UNDERSTOOD", templates.render("INTENT_UNCLEAR"))
    data = RequirementsAssistantData.model_validate(output.data)
    ctx.natural_reply = data.reply
    if data.scope in {"out_of_scope", "mixed", "unclear"}:
        return ConversationData(kind="conversation", reference_usage="none", evidence_ids=[])
    ctx.prepared_requirements = (content, output)
    return ConversationData(kind=data.kind, reference_usage=data.reference_usage, evidence_ids=[])


async def plan_visual(
    ctx: WorkflowContext,
    *,
    feedback: str | None = None,
    output: RoleOutput | None = None,
) -> None:
    from app.workflow.stages import s04, s05

    _, brief = await s04._require_approved_brief(ctx)
    reference = await s05._validate_sources(ctx, brief)
    expected_revision = ctx.project.revision
    source_dependencies = await s05._current_dependencies(
        ctx, ("brief_draft",), include_reference=reference is not None
    )
    previous_creative = await latest_artifact(ctx, "creative_direction")
    previous_visual = await latest_artifact(ctx, "visual_plan")
    if (
        not ctx.direct
        and output is None
        and feedback is None
        and previous_creative is not None
        and previous_creative.status == "approved"
        and previous_visual is not None
        and previous_visual.status == "approved"
    ):
        await set_stage(ctx, "S05", "active")
        await s05._finalize(ctx, {})
        return
    if not ctx.direct:
        await s04._ensure_budget(ctx)
        await s05._ensure_budget(ctx, "visual_plan")
    used, _ = await s05._budget_used(ctx, "visual_plan")
    if output is None:
        try:
            output = await run_role(
                ctx,
                "visual_planner",
                "S04",
                {
                    "brief_draft": brief,
                    "reference_report": reference,
                    "feedback": feedback,
                    "previous_creative": s04._json_dict(previous_creative.payload_json)
                    if previous_creative
                    else None,
                    "previous_visual": s05._payload(previous_visual) if previous_visual else None,
                },
                approved_inputs={"brief_draft": brief, "reference_report": reference},
                allowed_evidence_ids=tuple(s04._fact_evidence(brief)),
                attempt=used + 1,
            )
        except ApiError as exc:
            await set_stage(
                ctx, "S04", "failed", issue_code=exc.code, template_id="SERVICE_UNAVAILABLE"
            )
            raise
    if output.status != "ready":
        await s04._handle_non_ready(ctx, output)
        return
    candidate = VisualPlannerData.model_validate(output.require_data())
    creative = candidate.creative.model_dump(mode="json")
    visual = candidate.visual.model_dump(mode="json")
    try:
        if not ctx.direct:
            s04._validate_creative(brief, creative)
            s05._validate_visual_plan(visual, brief, creative)
        # Verify database facts after awaiting the model, before accepting either half.
        await s05._verify_dependencies(ctx, source_dependencies, expected_revision)
    except ApiError as exc:
        await set_stage(
            ctx, "S04", "blocked", issue_code=exc.code, template_id="ACTION_UNAVAILABLE"
        )
        raise
    creative_artifact = await start_artifact(ctx, "creative_direction", creative)
    await s04._mark_superseded(ctx, previous_creative)
    await s04._review_creative(ctx, brief, creative_artifact, creative)
    if ctx.run.stage != "S05" or ctx.run.status != "active":
        return
    dependencies = await s05._current_dependencies(
        ctx, s05._VISUAL_KINDS, include_reference=reference is not None
    )
    await s05._verify_dependencies(ctx, dependencies, expected_revision)
    visual_artifact = await start_artifact(ctx, "visual_plan", visual)
    await s05._supersede_previous(ctx, "visual_plan", visual_artifact.id)
    decision, checks = await s05._review_artifact(
        ctx,
        artifact=visual_artifact,
        target_kind="visual_plan",
        target_payload=visual,
        dependencies=dependencies,
        required_rules=s05.REQUIRED_VISUAL_RULES,
        approved_inputs={
            "brief_draft": brief,
            "creative_direction": creative,
            "reference_report": reference,
        },
        attempt=used + 1,
        expected_revision=expected_revision,
        extra_evidence=s05._collect_evidence(brief, creative, reference),
    )
    await s05._settle_review(
        ctx,
        visual_artifact,
        target_kind="visual_plan",
        decision=decision,
        checks=checks,
        dependencies=dependencies,
    )
    if decision == "pass":
        await s05._finalize(ctx, {})
