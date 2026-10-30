from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import Asset, Generation, Message, Project
from app.schemas import (
    AssetView,
    Brief,
    ErrorView,
    GenerationView,
    MessageView,
    ProjectView,
)
from app.services.retention import GenerationRetention
from app.workflow.access import ProjectAccessPolicy

PROJECT_FIELDS = (
    "product_description",
    "purpose",
    "style",
    "composition",
    "text_content",
)


def brief_from_project(project: Project) -> Brief:
    return Brief(
        product_description=project.product_description,
        purpose=project.purpose,
        style=project.style,
        composition=project.composition,
        text_content=project.text_content,
        image_size=project.image_size,
        reference_asset_id=project.reference_asset_id,
        prompt=project.prompt,
    )


def _empty_brief(project: Project) -> Brief:
    """旧需求卡不返回模型正文，只保留尺寸和输入引用等安全元信息。"""
    return Brief(
        image_size=project.image_size,
        reference_asset_id=project.reference_asset_id,
    )


def asset_availability(asset: Asset, data_dir: Path) -> str:
    candidate = (data_dir / asset.relative_path).resolve()
    try:
        candidate.relative_to(data_dir.resolve())
    except ValueError:
        return "missing"
    return "available" if candidate.is_file() else "missing"


def _asset_review_status(asset: Asset) -> str:
    if asset.kind == "reference":
        # 用户上传的输入图天然可预览，但绝不带“生成已批准”含义。
        return "reference"
    status = asset.review_status or "quarantined"
    if status not in {"quarantined", "approved", "rejected", "legacy_unreviewed"}:
        return "quarantined"
    return status


def asset_view(asset: Asset | None, data_dir: Path) -> AssetView | None:
    if asset is None:
        return None
    return AssetView(
        id=asset.id,
        kind=asset.kind,  # type: ignore[arg-type]
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        byte_size=asset.byte_size,
        created_at=asset.created_at,
        availability=asset_availability(asset, data_dir),  # type: ignore[arg-type]
        review_status=_asset_review_status(asset),  # type: ignore[arg-type]
    )


def error_view(code: str | None, message: str | None) -> ErrorView | None:
    if not code:
        return None
    return ErrorView(code=code, message=message or code)


def project_view(
    project: Project,
    reference_asset: Asset | None,
    data_dir: Path,
    *,
    policy: ProjectAccessPolicy | None = None,
) -> ProjectView:
    hidden = policy is not None and policy.project_output_hidden
    return ProjectView(
        id=project.id,
        title=project.title,
        revision=project.revision,
        prompt_revision=None if hidden else project.prompt_revision,
        confirmed_revision=None if hidden else project.confirmed_revision,
        brief_status="draft" if hidden else project.brief_status,  # type: ignore[arg-type]
        brief=_empty_brief(project) if hidden else brief_from_project(project),
        reference_asset=asset_view(reference_asset, data_dir),
        brief_error=(
            None if hidden else error_view(project.brief_error_code, project.brief_error_message)
        ),
        legacy_unreviewed=hidden,
        legacy_notice=policy.notice if hidden and policy is not None else None,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def message_view(
    message: Message,
    *,
    policy: ProjectAccessPolicy | None = None,
) -> MessageView:
    hidden = policy is not None and policy.hides_message(message.id)
    snapshot = None
    if not hidden and message.brief_snapshot_json:
        snapshot = Brief.model_validate(json.loads(message.brief_snapshot_json))
    return MessageView(
        id=message.id,
        sequence_no=message.sequence_no,
        role=message.role,  # type: ignore[arg-type]
        content=policy.notice if hidden and policy is not None else message.content,
        project_revision=message.project_revision,
        brief_snapshot=snapshot,
        legacy_unreviewed=hidden,
        legacy_notice=policy.notice if hidden and policy is not None else None,
        created_at=message.created_at,
    )


def generation_view(
    generation: Generation,
    result_asset: Asset | None,
    data_dir: Path,
    *,
    can_retrieve_result: bool = False,
    retrieval_unavailable_reason: str | None = None,
    policy: ProjectAccessPolicy | None = None,
    retention: GenerationRetention | None = None,
) -> GenerationView:
    hidden_generation = policy is not None and policy.hides_generation(generation.id)
    hidden_asset = policy is not None and policy.hides_asset(generation.result_asset_id)
    if hidden_generation:
        # 旧 succeeded/confirmed 不是审查通过：状态、提示词、远端任务与结果标识
        # 一律不返回，只保留版本号等安全元信息。
        return GenerationView(
            id=generation.id,
            project_id=generation.project_id,
            version_no=generation.version_no,
            request_id=generation.request_id,
            confirmed_revision=generation.confirmed_revision,
            status="unknown",
            stage="done",
            brief_snapshot=None,
            prompt="",
            reference_asset_id=generation.reference_asset_id,
            adapter_id=generation.adapter_id,
            model=generation.model,
            remote_task_id=None,
            result_asset_id=None,
            result_asset=None,
            error=None,
            can_retrieve_result=False,
            retrieval_unavailable_reason=None,
            legacy_unreviewed=True,
            legacy_notice=policy.notice if policy is not None else None,
            retention_status=retention.status if retention is not None else None,
            retention_expires_at=retention.expires_at if retention is not None else None,
            created_at=generation.created_at,
            started_at=generation.started_at,
            completed_at=generation.completed_at,
        )

    snapshot = (
        json.loads(generation.brief_snapshot_json) if generation.brief_snapshot_json else None
    )
    return GenerationView(
        id=generation.id,
        project_id=generation.project_id,
        version_no=generation.version_no,
        request_id=generation.request_id,
        confirmed_revision=generation.confirmed_revision,
        status=generation.status,  # type: ignore[arg-type]
        stage=generation.stage,  # type: ignore[arg-type]
        brief_snapshot=Brief.model_validate(snapshot) if snapshot else None,
        prompt=generation.prompt,
        reference_asset_id=generation.reference_asset_id,
        adapter_id=generation.adapter_id,
        model=generation.model,
        remote_task_id=generation.remote_task_id,
        result_asset_id=None if hidden_asset else generation.result_asset_id,
        result_asset=None if hidden_asset else asset_view(result_asset, data_dir),
        error=error_view(generation.error_code, generation.error_message),
        can_retrieve_result=can_retrieve_result,
        retrieval_unavailable_reason=retrieval_unavailable_reason,
        retention_status=retention.status if retention is not None else None,
        retention_expires_at=retention.expires_at if retention is not None else None,
        created_at=generation.created_at,
        started_at=generation.started_at,
        completed_at=generation.completed_at,
    )


def snapshot_json(brief: Brief) -> str:
    return json.dumps(brief.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
