from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.adapters.base import ProviderError, RoleRequest
from app.db import write_transaction
from app.errors import ApiError
from app.generation_guard import GenerationGuardError, ensure_project_owner_active
from app.timeutil import format_timestamp, utc_now
from app.workflow import adjudication, prompts, templates
from app.workflow.contracts import ContractError, digest_payload, validate_role_output
from app.workflow.state import WorkflowContext, latest_approval, record_diagnostic
from app.workflow.tables import DiagnosticEvent, RoleCall


@dataclass(frozen=True)
class RoleOutput:
    role: str
    status: str
    data: dict[str, Any] | None
    issues: list[dict[str, Any]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    model_fingerprint: str = "unavailable"
    role_call_id: str | None = None
    attempt: int = 1
    requirement_updates: list[dict[str, Any]] = field(default_factory=list)
    clarifications: list[dict[str, Any]] = field(default_factory=list)
    reply: str = ""
    conversation_only: bool = False
    document: str | None = None
    document_sources: list[dict[str, Any]] = field(default_factory=list)

    def require_data(self) -> dict[str, Any]:
        if self.status != "ready" or self.data is None:
            raise ApiError(
                409,
                "ROLE_NOT_READY",
                templates.render("ACTION_UNAVAILABLE"),
            )
        return self.data


async def _ensure_model_call_allowed(ctx: WorkflowContext) -> None:
    try:
        await ensure_project_owner_active(
            ctx.session_factory,
            ctx.project_id,
            principal_account_id=(
                ctx.principal.account_id if ctx.principal is not None else None
            ),
        )
    except GenerationGuardError as exc:
        raise ApiError(409, exc.code, exc.message) from exc


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _capability_ready(ctx: WorkflowContext, capability: str) -> tuple[Any, str]:
    """Return (LLM role config, reason) without ever exposing configuration values."""
    llm = ctx.settings.llm()
    if capability in {"TEXT", "VISION"}:
        if not llm.configured:
            return None, "配置不完整"
        if (llm.adapter_id or "") not in ctx.registry.known_llm_ids():
            return None, "适配器不可用"
        if capability == "VISION" and not llm.vision_model:
            return None, "多模态模型未配置"
        return llm, ""
    if capability == "IMAGE_GEN":
        visual = ctx.settings.visual()
        if not visual.configured:
            return None, "图片生成配置不完整"
        if (visual.adapter_id or "") not in ctx.registry.known_visual_ids():
            return None, "图片生成适配器不可用"
        capabilities = ctx.registry.visual_capabilities(visual.adapter_id or "")
        if capabilities is not None and capabilities.text_to_image is False:
            return None, "图片生成能力不可用"
        if not llm.configured:
            return None, "审查模型配置不完整"
        # VISION 是付费生成的前置能力；缺少显式视觉模型时必须在调用前阻断。
        if not llm.vision_model:
            return None, "视觉理解模型未配置"
        return llm, ""
    raise ValueError(f"unknown capability {capability}")


def _capability_error(capability: str, reason: str) -> ApiError:
    message = templates.render(
        "CAPABILITY_NOT_READY", capability_label=templates.capability_label(capability)
    )
    return ApiError(503, "CAPABILITY_NOT_READY", message)


async def _record_call(
    ctx: WorkflowContext,
    *,
    role: str,
    stage: str,
    capability: str,
    attempt: int,
    status: str,
    request_digest: str,
    request_json: str,
    response_json: str | None,
    error_code: str | None,
    error_message: str | None,
    model_fingerprint: str | None,
    started_at: str,
) -> RoleCall:
    call = RoleCall(
        id=str(uuid4()),
        run_id=ctx.run_id,
        project_id=ctx.project_id,
        stage=stage,
        role=role,
        capability=capability,
        attempt=attempt,
        status=status,
        request_digest=request_digest,
        request_json=request_json,
        response_json=response_json,
        error_code=error_code,
        error_message=(error_message or "")[:512] or None,
        model_fingerprint=(model_fingerprint or "")[:200] or None,
        started_at=started_at,
        finished_at=format_timestamp(utc_now()),
    )
    request = json.loads(request_json)
    payload = request.get("payload", {})
    metrics = {
        "role_call_id": call.id, "role": role, "stage": stage, "status": status,
        "request_chars": len(request_json),
        "section_chars": {key: len(json.dumps(value, ensure_ascii=False, sort_keys=True)) for key, value in payload.items()},
        "elapsed_seconds": round((datetime.fromisoformat(call.finished_at) - datetime.fromisoformat(started_at)).total_seconds(), 3),
    }
    async with write_transaction(ctx.session_factory) as session:
        session.add(call)
        session.add(DiagnosticEvent(
            id=str(uuid4()), project_id=ctx.project_id, event="role_request_metrics",
            detail_json=json.dumps(metrics, ensure_ascii=False, sort_keys=True),
            created_at=call.finished_at,
        ))
        await session.flush()
    return call


def _recovery_detail(
    ctx: WorkflowContext,
    *,
    role: str,
    stage: str,
    capability: str,
    attempt: int,
    status: str,
    request_digest: str,
    request_json: str,
    error_code: str | None,
    role_call_id: str | None,
    operation_id: str,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "operation_id": operation_id,
        "run_id": ctx.run_id,
        "project_id": ctx.project_id,
        "role": role,
        "stage": stage,
        "capability": capability,
        "phase": "review" if role == "output_reviewer" else "producer",
        "attempt": attempt,
        "status": status,
        "error_code": error_code,
        "request_digest": request_digest,
        "request_json": request_json,
        "role_call_id": role_call_id,
        "project_revision": ctx.project.revision,
        "requirement_revision": (
            ctx.run.requirement_revision
            if ctx.run is not None
            else ctx.project.revision
        ),
        "reference_asset_id": ctx.project.reference_asset_id,
    }
    try:
        parsed = json.loads(request_json)
        payload = parsed.get("payload") if isinstance(parsed, dict) else None
        if isinstance(payload, dict):
            detail["input_digest"] = digest_payload(payload.get("input_data"))
            detail["approved_inputs_digest"] = digest_payload(
                payload.get("approved_inputs") or []
            )
            input_data = payload.get("input_data")
            if isinstance(input_data, dict):
                detail["ruleset_version"] = input_data.get("ruleset_version")
            targets = payload.get("review_targets") or []
            if targets and isinstance(targets[0], dict):
                detail["target_id"] = targets[0].get("target_id")
                detail["target_kind"] = targets[0].get("target_kind")
    except ValueError:
        detail["input_digest"] = ""
    return detail


async def _record_recovery_checkpoint(
    ctx: WorkflowContext,
    *,
    role: str,
    stage: str,
    capability: str,
    attempt: int,
    status: str,
    request_digest: str,
    request_json: str,
    error_code: str | None,
    role_call_id: str | None,
    operation_id: str,
) -> None:
    """Persist one append-only checkpoint around exactly one role request.

    "started" is written before the adapter boundary so a hard process kill can
    be recognized without guessing the request. A later "completed" row closes
    the operation. This event is internal diagnostic state and never carries a
    provider URL, key, raw exception body or model reasoning.
    """
    detail = _recovery_detail(
        ctx,
        role=role,
        stage=stage,
        capability=capability,
        attempt=attempt,
        status=status,
        request_digest=request_digest,
        request_json=request_json,
        error_code=error_code,
        role_call_id=role_call_id,
        operation_id=operation_id,
    )
    await record_diagnostic(ctx, "recovery_checkpoint", detail)


async def _record_recovery_failure(
    ctx: WorkflowContext,
    *,
    role: str,
    stage: str,
    capability: str,
    attempt: int,
    status: str,
    request_digest: str,
    request_json: str,
    error_code: str,
    role_call_id: str | None,
    operation_id: str | None = None,
) -> None:
    """Persist the exact safe request needed for an explicit manual continue."""
    await _record_recovery_checkpoint(
        ctx,
        role=role,
        stage=stage,
        capability=capability,
        attempt=attempt,
        status=status,
        request_digest=request_digest,
        request_json=request_json,
        error_code=error_code,
        role_call_id=role_call_id,
        operation_id=operation_id or str(uuid4()),
    )


async def _run_request(
    ctx: WorkflowContext,
    role: str,
    stage: str,
    system_prompt: str,
    payload: dict[str, Any],
    *,
    images: tuple[Any, ...] = (),
    capability: str | None = None,
    attempt: int = 1,
) -> RoleOutput:
    """Run exactly one role request with strict validation and no retry.

    Capability and adapter checks happen after the request is assembled so a
    technical capability failure can still be recovered from its persisted
    request without guessing inputs.
    """
    resolved_capability = capability or ("VISION" if images else "TEXT")
    request = RoleRequest(
        system_prompt=system_prompt,
        payload=payload,
        images=tuple(images),
        output_format="markdown" if payload.get("handoff_format") == "markdown" else "json",
    )
    request_digest = digest_payload({"system": system_prompt, "payload": payload})
    request_json = json.dumps(
        {"system": system_prompt, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    started_at = format_timestamp(utc_now())
    operation_id = str(uuid4())

    llm, reason = _capability_ready(ctx, resolved_capability)
    if llm is None:
        await _record_recovery_failure(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="error",
            request_digest=request_digest,
            request_json=request_json,
            error_code="CAPABILITY_NOT_READY",
            role_call_id=None,
            operation_id=operation_id,
        )
        raise _capability_error(resolved_capability, reason)
    adapter = ctx.registry.build_llm(llm, ctx.http)
    if adapter is None:
        await _record_recovery_failure(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="error",
            request_digest=request_digest,
            request_json=request_json,
            error_code="CAPABILITY_NOT_READY",
            role_call_id=None,
            operation_id=operation_id,
        )
        raise _capability_error(resolved_capability, "适配器不可用")

    await _record_recovery_checkpoint(
        ctx,
        role=role,
        stage=stage,
        capability=resolved_capability,
        attempt=attempt,
        status="started",
        request_digest=request_digest,
        request_json=request_json,
        error_code=None,
        role_call_id=None,
        operation_id=operation_id,
    )
    try:
        response = await asyncio.wait_for(
            adapter.complete(request), timeout=llm.timeout_seconds
        )
    except TimeoutError:
        call = await _record_call(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="timeout",
            request_digest=request_digest,
            request_json=request_json,
            response_json=None,
            error_code="LLM_TIMEOUT",
            error_message="语言模型请求超时",
            model_fingerprint=None,
            started_at=started_at,
        )
        await _record_recovery_failure(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="timeout",
            request_digest=request_digest,
            request_json=request_json,
            error_code="LLM_TIMEOUT",
            role_call_id=call.id,
            operation_id=operation_id,
        )
        raise ApiError(
            503, "LLM_TIMEOUT", templates.render("LLM_TIMEOUT")
        ) from None
    except ProviderError as exc:
        timed_out = (exc.error_code or "") == "LLM_TIMEOUT"
        invalid = (exc.error_code or "") == "LLM_RESPONSE_INVALID"
        call = await _record_call(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="timeout" if timed_out else ("invalid" if invalid else "error"),
            request_digest=request_digest,
            request_json=request_json,
            response_json=None,
            error_code=exc.error_code,
            error_message=str(exc),
            model_fingerprint=None,
            started_at=started_at,
        )
        await _record_recovery_failure(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status=call.status,
            request_digest=request_digest,
            request_json=request_json,
            error_code=exc.error_code or "PROVIDER_ERROR",
            role_call_id=call.id,
            operation_id=operation_id,
        )
        public_code = "LLM_TIMEOUT" if timed_out else ("LLM_RESPONSE_INVALID" if invalid else "SERVICE_UNAVAILABLE")
        raise ApiError(503, public_code, templates.render(public_code)) from None

    document = response.text if request.output_format == "markdown" else None
    document_sources = payload.get("markdown_sources") or []
    if document is not None:
        from app.workflow import markdown_handoff

        # Preserve the original text before any program-owned projection.
        if role != "requirements_assistant":
            await markdown_handoff.save_document(ctx, role, document, document_sources)
        raw = markdown_handoff.project_output(ctx, role, document, payload)
    else:
        # Historical adapters and saved JSON calls keep their original contract.
        raw = response.payload
    try:
        envelope = validate_role_output(
            role, _normalize_envelope_placement(raw, role=role)
        )
    except ContractError as exc:
        call = await _record_call(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="invalid",
            request_digest=request_digest,
            request_json=request_json,
            response_json=json.dumps(raw, ensure_ascii=False, sort_keys=True),
            error_code="CONTRACT_INVALID",
            error_message=f"模型输出不符合 v2.0.0 契约：{exc}",
            model_fingerprint=response.model_fingerprint,
            started_at=started_at,
        )
        await _record_recovery_failure(
            ctx,
            role=role,
            stage=stage,
            capability=resolved_capability,
            attempt=attempt,
            status="invalid",
            request_digest=request_digest,
            request_json=request_json,
            error_code="CONTRACT_INVALID",
            role_call_id=call.id,
            operation_id=operation_id,
        )
        public_code = "LLM_RESPONSE_INVALID" if ctx.direct else "SERVICE_UNAVAILABLE"
        raise ApiError(503, public_code, templates.render(public_code)) from None

    dumped = envelope.model_dump(mode="json")
    call = await _record_call(
        ctx,
        role=role,
        stage=stage,
        capability=resolved_capability,
        attempt=attempt,
        status="ok",
        request_digest=request_digest,
        request_json=request_json,
        response_json=json.dumps(
            {"format": "markdown", "content": document, "projection": dumped}
            if document is not None else dumped, ensure_ascii=False, sort_keys=True,
        ),
        error_code=None,
        error_message=None,
        model_fingerprint=response.model_fingerprint,
        started_at=started_at,
    )
    await _record_recovery_checkpoint(
        ctx,
        role=role,
        stage=stage,
        capability=resolved_capability,
        attempt=attempt,
        status="completed",
        request_digest=request_digest,
        request_json=request_json,
        error_code=None,
        role_call_id=call.id,
        operation_id=operation_id,
    )
    return RoleOutput(
        role=role,
        status=str(dumped.get("status") or ""),
        data=dumped.get("data") if isinstance(dumped.get("data"), dict) else None,
        issues=dumped.get("issues") or [],
        questions=dumped.get("questions") or [],
        model_fingerprint=response.model_fingerprint,
        role_call_id=call.id,
        attempt=attempt,
        document=document,
        document_sources=document_sources,
    )


def _normalize_envelope_placement(raw: object, *, role: str | None = None) -> object:
    """Normalize harmless envelope placement and extra top-level fields.

    The role contract keeps issues/questions at the top level. Some providers
    place those lists inside data, add a descriptive top-level field such as
    ``questions_note``, or return the data object without the envelope. Placement
    is repaired and unknown top-level keys are dropped; every remaining value is
    still validated by the strict envelope.
    """

    if not isinstance(raw, dict):
        return raw
    allowed = {"role", "status", "data", "issues", "questions"}
    normalized = {key: value for key, value in raw.items() if key in allowed}
    if role is not None and not ({"role", "status", "data"} & normalized.keys()):
        data = {key: value for key, value in raw.items() if key not in {"issues", "questions"}}
        return {
            "role": role,
            "status": "ready",
            "data": data,
            "issues": raw.get("issues", []),
            "questions": raw.get("questions", []),
        }

    data = normalized.get("data")
    if not isinstance(data, dict):
        return normalized
    if "issues" in normalized or "questions" in normalized:
        return normalized
    if "issues" not in data and "questions" not in data:
        return normalized
    normalized_data = dict(data)
    normalized["issues"] = normalized_data.pop("issues", [])
    normalized["questions"] = normalized_data.pop("questions", [])
    normalized["data"] = normalized_data
    return normalized


async def run_role(
    ctx: WorkflowContext,
    role: str,
    stage: str,
    input_data: Any,
    approved_inputs: Any = None,
    review_targets: Any = None,
    repair_codes: list[str] | None = None,
    images: tuple[Any, ...] | None = None,
    capability: str | None = None,
    attempt: int = 1,
    required_rule_ids: tuple[str, ...] = (),
    allowed_evidence_ids: tuple[str, ...] = (),
) -> RoleOutput:
    """Run exactly one fresh model role call, with strict validation and no retry."""
    if ctx.direct and role not in prompts.ACTIVE_ROLE_INCREMENTS:
        raise ApiError(409, "ROLE_RETIRED", "当前流程只使用四个协作角色。")
    if (
        role in {"output_reviewer", "quality_compliance"}
        and adjudication.gate_mode_for_target("legacy_review") == "off"
    ):
        raise ApiError(409, "ROLE_RETIRED", "该审查角色已下线，请使用当前四角色流程。")
    await _ensure_model_call_allowed(ctx)
    generation_authorized = await latest_approval(ctx, "generation_prompt") is not None
    size = ctx.project.image_size or ctx.settings.visual().image_size
    payload: dict[str, Any] = {
        "task": {
            "run_id": ctx.run_id,
            "revision": (
                ctx.run.requirement_revision if ctx.run is not None else ctx.project.revision
            ),
            "stage": stage,
            "role": role,
            "attempt": attempt,
            "required_rule_ids": list(required_rule_ids),
            "allowed_evidence_ids": list(allowed_evidence_ids),
        },
        "state": {
            "brief_confirmed": (
                ctx.project.brief_status == "confirmed"
                and ctx.project.confirmed_revision == ctx.project.revision
            ),
            "generation_authorized": generation_authorized,
        },
        "capabilities": {"size": size, "count": 1},
        "input_data": _jsonable(input_data),
        "approved_inputs": _jsonable(approved_inputs if approved_inputs is not None else []),
        "review_targets": _jsonable(review_targets or []),
        "repair_codes": list(repair_codes or []),
    }
    system = prompts.build_system_prompt(role, direct=ctx.direct)
    if ctx.direct and ctx.settings.workflow_handoff_format == "markdown":
        from app.workflow import markdown_handoff

        context, sources = await markdown_handoff.build_context(ctx, role, input_data, approved_inputs)
        payload.update(handoff_format="markdown", markdown_context=context, markdown_sources=sources)
        system = markdown_handoff.system_prompt(role)
    return await _run_request(
        ctx,
        role,
        stage,
        system,
        payload,
        images=tuple(images or ()),
        capability=capability,
        attempt=attempt,
    )


async def run_role_request(
    ctx: WorkflowContext,
    role: str,
    stage: str,
    request_json: str,
    *,
    images: tuple[Any, ...] = (),
    capability: str | None = None,
    attempt: int = 1,
) -> RoleOutput:
    """Replay the exact persisted role request after an explicit manual continue.

    This is not an automatic retry: callers must have checked a persisted
    recovery checkpoint and the user's explicit action first. The stored request
    is not regenerated from mutable inputs.
    """
    await _ensure_model_call_allowed(ctx)
    try:
        parsed = json.loads(request_json)
    except ValueError:
        raise ApiError(
            409, "RECOVERY_INVALID", templates.render("ACTION_UNAVAILABLE")
        ) from None
    if not isinstance(parsed, dict):
        raise ApiError(
            409, "RECOVERY_INVALID", templates.render("ACTION_UNAVAILABLE")
        )
    system_prompt = parsed.get("system")
    payload = parsed.get("payload")
    if not isinstance(system_prompt, str) or not isinstance(payload, dict):
        raise ApiError(
            409, "RECOVERY_INVALID", templates.render("ACTION_UNAVAILABLE")
        )
    return await _run_request(
        ctx,
        role,
        stage,
        system_prompt,
        payload,
        images=tuple(images or ()),
        capability=capability,
        attempt=attempt,
    )


__all__ = ["RoleOutput", "run_role", "run_role_request"]
