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


def asset_availability(asset: Asset, data_dir: Path) -> str:
    candidate = (data_dir / asset.relative_path).resolve()
    try:
        candidate.relative_to(data_dir.resolve())
    except ValueError:
        return "missing"
    return "available" if candidate.is_file() else "missing"


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
    )


def error_view(code: str | None, message: str | None) -> ErrorView | None:
    if not code:
        return None
    return ErrorView(code=code, message=message or code)


def project_view(project: Project, reference_asset: Asset | None, data_dir: Path) -> ProjectView:
    return ProjectView(
        id=project.id,
        title=project.title,
        revision=project.revision,
        prompt_revision=project.prompt_revision,
        confirmed_revision=project.confirmed_revision,
        brief_status=project.brief_status,  # type: ignore[arg-type]
        brief=brief_from_project(project),
        reference_asset=asset_view(reference_asset, data_dir),
        brief_error=error_view(project.brief_error_code, project.brief_error_message),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def message_view(message: Message) -> MessageView:
    snapshot = None
    if message.brief_snapshot_json:
        snapshot = Brief.model_validate(json.loads(message.brief_snapshot_json))
    return MessageView(
        id=message.id,
        sequence_no=message.sequence_no,
        role=message.role,  # type: ignore[arg-type]
        content=message.content,
        project_revision=message.project_revision,
        brief_snapshot=snapshot,
        created_at=message.created_at,
    )


def generation_view(
    generation: Generation,
    result_asset: Asset | None,
    data_dir: Path,
    *,
    can_retrieve_result: bool = False,
    retrieval_unavailable_reason: str | None = None,
) -> GenerationView:
    snapshot = json.loads(generation.brief_snapshot_json) if generation.brief_snapshot_json else None
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
        result_asset_id=generation.result_asset_id,
        result_asset=asset_view(result_asset, data_dir),
        error=error_view(generation.error_code, generation.error_message),
        can_retrieve_result=can_retrieve_result,
        retrieval_unavailable_reason=retrieval_unavailable_reason,
        created_at=generation.created_at,
        started_at=generation.started_at,
        completed_at=generation.completed_at,
    )


def snapshot_json(brief: Brief) -> str:
    return json.dumps(brief.model_dump(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
