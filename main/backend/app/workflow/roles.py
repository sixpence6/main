from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.adapters.base import ProviderError, RoleRequest
from app.db import write_transaction
from app.errors import ApiError
from app.timeutil import format_timestamp, utc_now
from app.workflow import prompts, templates
from app.workflow.contracts import ContractError, digest_payload, validate_role_output
from app.workflow.state import WorkflowContext, latest_approval
from app.workflow.tables import RoleCall


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

    def require_data(self) -> dict[str, Any]:
        if self.status != "ready" or self.data is None:
            raise ApiError(
                409,
                "ROLE_NOT_READY",
                templates.render("ACTION_UNAVAILABLE"),
            )
        return self.data


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
    async with write_transaction(ctx.session_factory) as session:
        session.add(call)
        await session.flush()
    return call


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
    """Run exactly one model role call, with strict validation and no retry."""
    resolved_capability = capability or ("VISION" if images else "TEXT")
    llm, reason = _capability_ready(ctx, resolved_capability)
    if llm is None:
        raise _capability_error(resolved_capability, reason)
    adapter = ctx.registry.build_llm(llm, ctx.http)
    if adapter is None:
        raise _capability_error(resolved_capability, "适配器不可用")

    generation_authorized = await latest_approval(ctx, "generation_prompt") is not None
    size = ctx.project.image_size or ctx.settings.visual().image_size
    payload: dict[str, Any] = {
        "task": {
            "run_id": ctx.run_id,
            "revision": ctx.run.requirement_revision if ctx.run is not None else ctx.project.revision,
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
        "approved_inputs": _jsonable(approved_inputs or {}),
        "review_targets": _jsonable(review_targets or []),
        "repair_codes": list(repair_codes or []),
    }
    system_prompt = prompts.build_system_prompt(role)
    request = RoleRequest(
        system_prompt=system_prompt,
        payload=payload,
        images=tuple(images or ()),
    )
    request_digest = digest_payload({"system": system_prompt, "payload": payload})
    request_json = json.dumps(
        {"system": system_prompt, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    started_at = format_timestamp(utc_now())

    try:
        response = await asyncio.wait_for(
            adapter.complete(request), timeout=llm.timeout_seconds
        )
    except asyncio.TimeoutError:
        await _record_call(
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
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE")
        ) from None
    except ProviderError as exc:
        timed_out = (exc.error_code or "") == "LLM_TIMEOUT"
        invalid = (exc.error_code or "") == "LLM_RESPONSE_INVALID"
        await _record_call(
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
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE")
        ) from None

    raw = response.payload
    try:
        envelope = validate_role_output(role, raw)
    except ContractError:
        await _record_call(
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
            error_message="模型输出不符合 v2.0.0 契约",
            model_fingerprint=response.model_fingerprint,
            started_at=started_at,
        )
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE")
        ) from None

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
        response_json=json.dumps(dumped, ensure_ascii=False, sort_keys=True),
        error_code=None,
        error_message=None,
        model_fingerprint=response.model_fingerprint,
        started_at=started_at,
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
    )


__all__ = ["RoleOutput", "run_role"]
