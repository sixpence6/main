from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Asset, Generation, Project
from app.services.assets import delete_asset_file
from app.services.generations import _decode_outcome_image, _validate_result_bytes, parse_image_size
from app.services.retrieval import load_retrieval_info
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.contracts import digest_payload
from app.workflow.state import add_approval, add_review
from app.workflow.tables import Delivery, GenerationJob


def _payload_generation(payload: dict[str, Any]) -> str | None:
    if set(payload) - {"generation_id"}:
        raise validation_error("请求参数不合法")
    value = payload.get("generation_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise validation_error("请选择需要找回的生成记录")
    return value


async def _select(ctx, payload: dict[str, Any]) -> Generation:
    generation_id = _payload_generation(payload)
    async with read_session(ctx.session_factory) as session:
        if generation_id is not None:
            generation = await session.get(Generation, generation_id)
        else:
            generation = await session.scalar(
                select(Generation)
                .where(
                    Generation.project_id == ctx.project_id,
                    Generation.status == "failed",
                    Generation.error_code == "RESULT_FETCH_FAILED",
                )
                .order_by(Generation.version_no.desc())
                .limit(1)
            )
    if generation is None or generation.project_id != ctx.project_id:
        raise conflict("RESULT_NOT_RETRIEVABLE", templates.render("ACTION_UNAVAILABLE"))
    return generation


def _raise_info(info: Any) -> None:
    if info.failure_code == "ADAPTER_UNAVAILABLE":
        raise ApiError(503, "ADAPTER_UNAVAILABLE", info.reason or "适配器不可用")
    if info.failure_code in {"RETRIEVE_CONFIG_CHANGED", "PROJECT_BUSY"}:
        raise conflict(info.failure_code, info.reason or templates.render("ACTION_UNAVAILABLE"))
    raise conflict("RESULT_NOT_RETRIEVABLE", info.reason or templates.render("ACTION_UNAVAILABLE"))


async def _publish(
    ctx,
    *,
    generation,
    asset,
    job_id,
    digest,
    delivery_data,
    delivery_artifact,
    quality_artifact,
    checks,
    rules,
    model_fingerprint,
    snapshot_digest,
    reference_digest,
) -> None:
    now = format_timestamp(utc_now())
    filename = f"{ctx.project_id}-v{generation.version_no}.png"[:120]
    caption = str(delivery_data.get("caption") or "")[:800]
    await add_approval(
        ctx,
        approval_type="quality_report",
        artifact=quality_artifact,
        approved_by="output_reviewer",
        checks={"target_id": f"quality_report:{asset.id}"},
        requirement_digest=snapshot_digest,
        material_digest=reference_digest,
        model_fingerprint=model_fingerprint,
    )
    await add_approval(
        ctx,
        approval_type="image",
        artifact=quality_artifact,
        approved_by="output_reviewer",
        target_digest=digest,
        checks={
            "asset_id": asset.id,
            "asset_digest": digest,
            "quality_report_id": quality_artifact.id,
            "source_generation_id": generation.id,
            "rule_ids": list(rules),
            "checks": checks,
        },
        requirement_digest=snapshot_digest,
        material_digest=reference_digest,
        model_fingerprint=model_fingerprint,
    )
    await add_approval(
        ctx,
        approval_type="delivery_manifest",
        artifact=delivery_artifact,
        approved_by="output_reviewer",
        checks={
            "asset_id": asset.id,
            "asset_digest": digest,
            "manifest_artifact_id": delivery_artifact.id,
            "source_generation_id": generation.id,
        },
        requirement_digest=snapshot_digest,
        material_digest=reference_digest,
        model_fingerprint=model_fingerprint,
    )
    async with write_transaction(ctx.session_factory) as session:
        current = await session.get(Generation, generation.id)
        latest_version = await session.scalar(
            select(Generation.version_no)
            .where(Generation.project_id == ctx.project_id)
            .order_by(Generation.version_no.desc())
            .limit(1)
        )
        if (
            current is None
            or current.status != "failed"
            or current.error_code != "RESULT_FETCH_FAILED"
            or current.version_no != generation.version_no
            or latest_version != generation.version_no
        ):
            raise conflict("RUN_SUPERSEDED", templates.render("RUN_SUPERSEDED"))
        job = await session.get(GenerationJob, job_id)
        stored_asset = await session.get(Asset, asset.id)
        if job is None or stored_asset is None:
            raise conflict("RESULT_NOT_RETRIEVABLE", templates.render("ACTION_UNAVAILABLE"))
        job.status = "published"
        job.result_asset_id = asset.id
        job.updated_at = now
        stored_asset.sha256 = digest
        stored_asset.review_status = "approved"
        stored_asset.review_note = "retrieved_reviewed"
        current.result_asset_id = asset.id
        current.status = "succeeded"
        current.stage = "done"
        current.error_code = None
        current.error_message = None
        current.completed_at = now
        delivery = await session.scalar(
            select(Delivery).where(Delivery.asset_id == asset.id).limit(1)
        )
        if delivery is None:
            session.add(
                Delivery(
                    id=str(uuid4()),
                    project_id=ctx.project_id,
                    generation_job_id=job.id,
                    asset_id=asset.id,
                    manifest_artifact_id=delivery_artifact.id,
                    status="published",
                    filename=filename,
                    caption=caption,
                    asset_digest=digest,
                    approved_at=now,
                    published_at=now,
                    created_at=now,
                )
            )
        else:
            delivery.generation_job_id = job.id
            delivery.manifest_artifact_id = delivery_artifact.id
            delivery.status = "published"
            delivery.filename = filename
            delivery.caption = caption
            delivery.asset_digest = digest
            delivery.approved_at = now
            delivery.published_at = now
        await session.flush()


async def _latest_job(ctx, asset_id: str) -> GenerationJob | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == ctx.run_id, GenerationJob.result_asset_id == asset_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )


async def retrieve(ctx, payload: dict[str, Any]) -> None:
    from app.workflow.stages import s11

    generation = await _select(ctx, payload)
    if (
        generation.status == "succeeded"
        and generation.result_asset_id
        and await s11._asset_is_still_available(ctx, generation.result_asset_id)
    ):
        await s11._finish(ctx)
        return
    info = await load_retrieval_info(ctx.session_factory, ctx.settings, ctx.registry, generation)
    if not info.can_retrieve or info.locator is None:
        _raise_info(info)
    await s11._ensure_run(ctx)
    snapshot = s11._parse_snapshot(generation.brief_snapshot_json)
    if snapshot is None:
        await s11._block_stage(ctx, "LEGACY_SNAPSHOT_MISSING")
        return
    reference = await s11._load_reference(ctx, generation.reference_asset_id)
    if reference is None and generation.reference_asset_id is not None:
        return
    reference_digest = (
        reference[2] if reference is not None else digest_payload({"reference": "none"})
    )
    expected = parse_image_size(generation.image_size)
    if expected is None:
        await s11._block_stage(ctx, "RESULT_NOT_RETRIEVABLE")
        return
    adapter = ctx.registry.build_visual(ctx.settings.visual(), ctx.http)
    if adapter is None:
        raise ApiError(503, "ADAPTER_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE"))
    try:
        async with asyncio.timeout(ctx.settings.visual().timeout_seconds):
            fetched = await adapter.fetch_result(info.locator)
    except TimeoutError:
        raise ApiError(504, "RESULT_FETCH_TIMEOUT", "重取结果超时，请稍后重试") from None
    except Exception:
        raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果失败，请稍后重试") from None
    if fetched.state != "succeeded":
        raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果失败，请稍后重试")
    try:
        data = _decode_outcome_image(fetched)
    except Exception:
        data = None
    if data is None:
        raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果失败，请稍后重试")
    try:
        image_info, relative_path, asset_id = await asyncio.to_thread(
            _validate_result_bytes, ctx.settings, data, expected
        )
    except Exception:
        raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果无法作为图片保存") from None
    digest = hashlib.sha256(data).hexdigest()
    snapshot_digest = s11._snapshot_digest(snapshot)
    start_revision = ctx.project.revision
    now = format_timestamp(utc_now())
    try:
        async with write_transaction(ctx.session_factory) as session:
            current = await session.get(Generation, generation.id)
            project = await session.get(Project, ctx.project_id)
            latest_version = await session.scalar(
                select(Generation.version_no)
                .where(Generation.project_id == ctx.project_id)
                .order_by(Generation.version_no.desc())
                .limit(1)
            )
            if (
                current is None
                or current.status != "failed"
                or current.error_code != "RESULT_FETCH_FAILED"
                or current.version_no != generation.version_no
                or project is None
                or project.revision != start_revision
                or latest_version != generation.version_no
            ):
                raise conflict("RUN_SUPERSEDED", templates.render("RUN_SUPERSEDED"))
            asset = Asset(
                id=asset_id,
                project_id=ctx.project_id,
                kind="generated",
                relative_path=relative_path,
                mime_type=image_info.mime_type,
                width=image_info.width,
                height=image_info.height,
                byte_size=len(data),
                sha256=digest,
                review_status="quarantined",
                review_note=None,
                created_at=now,
            )
            job = GenerationJob(
                id=str(uuid4()),
                project_id=ctx.project_id,
                run_id=ctx.run_id,
                generation_id=current.id,
                request_id=str(uuid4()),
                request_digest=digest_payload({"generation_id": current.id, "asset_id": asset_id}),
                prompt_artifact_id=None,
                confirmed_snapshot_digest=snapshot_digest,
                status="result_fetched",
                submit_attempted=0,
                remote_task_id=None,
                result_asset_id=asset_id,
                error_code=None,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
            session.add(asset)
            session.add(job)
            await session.flush()
    except ApiError:
        await asyncio.to_thread(delete_asset_file, ctx.settings, relative_path)
        await s11._block_stage(ctx, "RUN_SUPERSEDED", "RUN_SUPERSEDED")
        return
    rules = s11._image_rules(snapshot, reference is not None)
    quality_result = await s11._quality_review(
        ctx,
        asset=asset,
        asset_bytes=data,
        asset_digest=digest,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        reference=reference,
        rules=rules,
    )
    if quality_result is None:
        return
    quality, quality_artifact = quality_result
    report_review = await s11._review_quality_report(
        ctx,
        artifact=quality_artifact,
        quality=quality,
        asset=asset,
        asset_bytes=data,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        reference=reference,
    )
    if report_review is None:
        return
    decision, report_checks, _ = report_review
    if decision != "pass":
        await add_review(
            ctx,
            quality_artifact,
            reviewer_role="output_reviewer",
            decision=decision,
            checks={"checks": report_checks, "rule_ids": list(s11.LEGACY_REPORT_RULES)},
        )
        await s11._block_stage(ctx, f"REVIEW_{decision.upper()}", "REVIEW_UNAVAILABLE")
        return
    await add_review(
        ctx,
        quality_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={"checks": report_checks, "rule_ids": list(s11.LEGACY_REPORT_RULES)},
    )
    quality_artifact.status = "approved"
    image_review = await s11._review_image_target(
        ctx,
        asset=asset,
        asset_bytes=data,
        asset_digest=digest,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        quality=quality,
        quality_artifact=quality_artifact,
        reference=reference,
        rules=rules,
    )
    if image_review is None:
        return
    image_decision, image_checks, image_output = image_review
    if image_decision != "pass":
        await s11._block_stage(ctx, f"REVIEW_{image_decision.upper()}", "REVIEW_UNAVAILABLE")
        return
    requirement_revision = max(1, int(generation.confirmed_revision or 1))
    delivery_result = await s11._delivery_review(
        ctx,
        asset=asset,
        asset_digest=digest,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        quality=quality,
        requirement_revision=requirement_revision,
    )
    if delivery_result is None:
        return
    delivery_data, delivery_artifact = delivery_result
    delivery_artifact.status = "approved"
    job = await _latest_job(ctx, asset.id)
    if job is None:
        await s11._block_stage(ctx, "RESULT_NOT_RETRIEVABLE")
        return
    try:
        await _publish(
            ctx,
            generation=generation,
            asset=asset,
            job_id=job.id,
            digest=digest,
            delivery_data=delivery_data,
            delivery_artifact=delivery_artifact,
            quality_artifact=quality_artifact,
            checks=image_checks,
            rules=rules,
            model_fingerprint=image_output.model_fingerprint,
            snapshot_digest=snapshot_digest,
            reference_digest=reference_digest,
        )
    except ApiError as exc:
        await s11._block_stage(
            ctx,
            exc.code,
            "RUN_SUPERSEDED" if exc.code == "RUN_SUPERSEDED" else "REVIEW_UNAVAILABLE",
        )
        return
    await s11._finish(ctx)
