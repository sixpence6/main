from __future__ import annotations

import json
from typing import Any

from app.models import Project
from app.workflow import templates
from app.workflow.reasons import ReasonCode, classify_issue_code
from app.workflow.schemas_workflow import (
    ArtifactView,
    ConstraintProjectionView,
    GenerationIntentView,
    ResourceStateView,
    SafeReasonView,
    TaskBindingView,
    TaskStateView,
    WorkflowProgressView,
    WorkflowVersionView,
)
from app.workflow.tables import Delivery, GenerationJob, WorkflowRun

_PHASE_BY_STAGE = {
    "S01": "intake",
    "S02": "intake",
    "S03": "requirements",
    "S04": "requirements",
    "S05": "requirements",
    "S06": "requirements",
    "S07": "prompt",
    "S08": "generation",
    "S09": "review",
    "S10": "review",
    "S11": "delivery",
    "S12": "recovery",
}

_LABELS = {
    "product_description": "商品主体",
    "purpose": "用途",
    "facts": "事实",
    "creative_direction": "创意方向",
    "style_keywords": "风格",
    "composition": "构图",
    "lighting": "光线",
    "background": "背景",
    "palette": "色彩",
    "text_layout": "文字排版",
    "reference_treatment": "参考处理",
    "preservation_rules": "保留规则",
    "texts": "文字",
    "must_include": "必须包含",
    "must_preserve": "必须保留",
    "forbidden": "禁止项",
    "size": "尺寸",
    "count": "数量",
    "reference_asset_id": "参考图",
    "reference_usage": "参考用途",
    "selling_points": "卖点",
    "copy_lines": "文案",
    "assumptions": "假设",
    "unresolved": "待解决",
}

_SAFE_REASON_COPY = {
    ReasonCode.MISSING_INFORMATION: "还需要补充信息后才能继续。",
    ReasonCode.REPAIRABLE_COMPOSITION: "内容需要调整后可重新审查。",
    ReasonCode.TRUE_DRIFT: "当前内容与已确认需求不一致，需要重新确认。",
    ReasonCode.CONTENT_REJECTION: "本次内容未通过检查，未放行。",
    ReasonCode.MISSING_EVIDENCE: "缺少必要证据，暂不能继续。",
    ReasonCode.PROTOCOL_ERROR: "处理结果格式异常，已停止并保留当前状态。",
    ReasonCode.TECHNICAL_FAILURE: "处理服务暂不可用，本次未自动重试。",
    ReasonCode.REMOTE_UNKNOWN: "远端状态尚未确认，系统不会自动重发。",
    ReasonCode.AUTHORIZATION_INVALID: "当前授权已失效，不能访问此内容。",
}

_SEVERITY = {
    ReasonCode.MISSING_INFORMATION: "needs_input",
    ReasonCode.REPAIRABLE_COMPOSITION: "blocked",
    ReasonCode.TRUE_DRIFT: "blocked",
    ReasonCode.CONTENT_REJECTION: "failed",
    ReasonCode.MISSING_EVIDENCE: "blocked",
    ReasonCode.PROTOCOL_ERROR: "blocked",
    ReasonCode.TECHNICAL_FAILURE: "failed",
    ReasonCode.REMOTE_UNKNOWN: "unknown",
    ReasonCode.AUTHORIZATION_INVALID: "blocked",
}

_USER_INPUT_CATEGORIES = frozenset(
    {
        ReasonCode.MISSING_INFORMATION,
        ReasonCode.REPAIRABLE_COMPOSITION,
        ReasonCode.TRUE_DRIFT,
        ReasonCode.CONTENT_REJECTION,
        ReasonCode.MISSING_EVIDENCE,
        ReasonCode.AUTHORIZATION_INVALID,
    }
)


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _label(path: str, index: int | None) -> str:
    base = _LABELS.get(path, path)
    return base if index is None else f"{base} #{index + 1}"


def _action_status(run: WorkflowRun | None) -> str:
    return "none" if run is None else run.status


def _progress(run: WorkflowRun | None) -> WorkflowProgressView:
    if run is None:
        return WorkflowProgressView()
    return WorkflowProgressView(
        phase=_PHASE_BY_STAGE.get(run.stage, "recovery"),
        stage=run.stage,
        stage_label=templates.stage_label(run.stage),
        status=run.status,
        awaiting_user=run.status in {"needs_input", "blocked"},
        terminal=run.status in {"completed", "failed"},
    )


def _safe_reason(run: WorkflowRun | None) -> SafeReasonView | None:
    if run is None or run.status not in {"needs_input", "blocked", "failed"}:
        return None
    code = run.issue_code or run.template_id
    if not code:
        return None
    category = classify_issue_code(code)
    template_id = run.template_id if run.template_id in templates.TEMPLATES else None
    params = _json_dict(run.template_params_json) if template_id else {}
    safe_params = {str(key): str(value) for key, value in params.items()}
    message = templates.render(template_id, **safe_params) if template_id else None
    if not message:
        # Never read run.issue_message here. That field belongs to the legacy
        # human-readable issue surface and may contain reviewer prose.
        safe_params = {}
        message = _SAFE_REASON_COPY[category]
    severity = _SEVERITY[category]
    return SafeReasonView(
        category=category.value,
        code=code,
        message=message,
        template_id=template_id,
        params=safe_params,
        severity=severity,
        requires_user_input=category in _USER_INPUT_CATEGORIES,
        requires_new_version=category in {
            ReasonCode.TRUE_DRIFT,
            ReasonCode.CONTENT_REJECTION,
        },
        requires_evidence=category == ReasonCode.MISSING_EVIDENCE,
        remote_unknown=category == ReasonCode.REMOTE_UNKNOWN,
        auto_retry=False,
    )


def _binding(
    *,
    project: Project,
    run: WorkflowRun | None,
    job: GenerationJob | None,
    delivery: Delivery | None,
    intent: GenerationIntentView | None,
    active_snapshot: dict[str, Any] | None,
) -> TaskBindingView:
    active_digest = (
        active_snapshot.get("snapshot_digest")
        if isinstance(active_snapshot, dict)
        else None
    )
    if job is None:
        current = True
    elif run is None:
        current = False
    else:
        current = project.revision == run.requirement_revision and (
            active_digest is None or job.confirmed_snapshot_digest == active_digest
        )
    return TaskBindingView(
        project_id=project.id,
        run_id=run.id if run is not None else None,
        project_revision=project.revision,
        requirement_revision=(
            run.requirement_revision if run is not None else project.revision
        ),
        intent_id=intent.id if intent is not None else None,
        intent_status=intent.status if intent is not None else None,
        intent_requirement_revision=(
            intent.requirement_revision if intent is not None else None
        ),
        generation_job_id=job.id if job is not None else None,
        generation_job_status=job.status if job is not None else None,
        confirmed_snapshot_digest=(
            job.confirmed_snapshot_digest if job is not None else None
        ),
        execution_snapshot_digest=(
            str(active_digest) if active_digest is not None else None
        ),
        delivery_id=delivery.id if delivery is not None else None,
        delivery_status=delivery.status if delivery is not None else None,
        current=current,
    )


def _constraints(
    active_snapshot: dict[str, Any] | None,
    artifacts: list[ArtifactView],
) -> list[ConstraintProjectionView]:
    constraints = (
        active_snapshot.get("constraints")
        if isinstance(active_snapshot, dict)
        else None
    )
    if isinstance(constraints, list):
        result: list[ConstraintProjectionView] = []
        for item in constraints:
            if not isinstance(item, dict):
                continue
            strength = item.get("strength")
            path = item.get("path")
            if strength not in {"hard", "soft"} or not isinstance(path, str):
                continue
            index = item.get("index")
            result.append(
                ConstraintProjectionView(
                    constraint_id=str(item.get("constraint_id") or f"req:{path}"),
                    path=path,
                    label=_label(path, index if isinstance(index, int) else None),
                    strength=strength,
                    source=(
                        item.get("source")
                        if isinstance(item.get("source"), str)
                        else None
                    ),
                    user_lock_basis=(
                        item.get("user_lock_basis")
                        if isinstance(item.get("user_lock_basis"), str)
                        else None
                    ),
                    rule_id=(
                        item.get("rule_id")
                        if isinstance(item.get("rule_id"), str)
                        else None
                    ),
                    value=item.get("original_value"),
                )
            )
        if result:
            return result

    approved = [
        artifact
        for artifact in artifacts
        if artifact.kind == "final_brief" and artifact.status == "approved"
    ]
    if not approved:
        return []
    payload = approved[-1].payload or {}
    strengths = payload.get("field_strengths")
    if not isinstance(strengths, dict):
        return []
    locks = payload.get("user_locks")
    locks = locks if isinstance(locks, dict) else {}
    result = []
    for path, strength in strengths.items():
        if strength not in {"hard", "soft"}:
            continue
        result.append(
            ConstraintProjectionView(
                constraint_id=f"draft:{path}",
                path=str(path),
                label=_label(str(path), None),
                strength=strength,
                source="final_brief",
                user_lock_basis=(
                    locks.get(path) if isinstance(locks.get(path), str) else None
                ),
                rule_id=None,
                value=payload.get(path),
            )
        )
    return result


def _resource_state(
    *,
    run: WorkflowRun | None,
    job: GenerationJob | None,
    delivery: Delivery | None,
    versions: list[WorkflowVersionView],
    binding: TaskBindingView,
) -> ResourceStateView:
    current_version = None
    if binding.current and job is not None and delivery is not None:
        current_version = next(
            (
                item
                for item in versions
                if item.generation_job_id == job.id and item.asset_id == delivery.asset_id
            ),
            None,
        )
    history = [
        item for item in versions if current_version is None or item is not current_version
    ]
    if not binding.current and versions:
        state = "history_only"
    elif (
        current_version is not None
        and delivery is not None
        and delivery.status == "published"
    ):
        state = "published"
    elif delivery is not None and delivery.status == "approved":
        state = "pending"
    elif job is not None:
        if job.status in {"failed", "rejected"}:
            state = "failed"
        elif job.status == "unknown":
            state = "unknown"
        elif job.status == "quarantined":
            state = "blocked"
        elif job.status in {
            "created",
            "submit_pending",
            "submitted",
            "remote_pending",
            "result_fetched",
            "approved",
        }:
            state = "pending"
        elif job.status == "published":
            state = "published" if current_version is not None else "history_only"
        else:
            state = "unknown"
    elif versions:
        state = "history_only"
    else:
        state = "none"
    return ResourceStateView(
        current_run_id=run.id if run is not None else None,
        current_state=state,
        current_version=current_version,
        history=history,
    )


def build_task_state(
    *,
    project: Project,
    run: WorkflowRun | None,
    job: GenerationJob | None,
    delivery: Delivery | None,
    versions: list[WorkflowVersionView],
    intent: GenerationIntentView | None,
    active_snapshot: dict[str, Any] | None,
    artifacts: list[ArtifactView],
) -> TaskStateView:
    binding = _binding(
        project=project,
        run=run,
        job=job,
        delivery=delivery,
        intent=intent,
        active_snapshot=active_snapshot,
    )
    return TaskStateView(
        progress=_progress(run),
        reason=_safe_reason(run),
        binding=binding,
        constraints=_constraints(active_snapshot, artifacts),
        resource_state=_resource_state(
            run=run,
            job=job,
            delivery=delivery,
            versions=versions,
            binding=binding,
        ),
    )


__all__ = ["build_task_state"]
