from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.config import Settings
from app.db import write_transaction
from app.main import create_app
from app.models import Asset, Generation, Project
from app.services import retention
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import (
    Approval,
    Artifact,
    ArtifactReview,
    Delivery,
    DiagnosticEvent,
    GenerationJob,
    LegacyItem,
    WorkflowRun,
)
from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _ts(moment: datetime) -> str:
    return format_timestamp(moment)


@asynccontextmanager
async def _live_app(settings: Settings, registry):
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield application, client


@dataclass
class Seeded:
    project_id: str
    generation_id: str
    asset_id: str
    path: Path
    data: bytes


async def _create_project(session, *, title: str = "T20 项目") -> Project:
    now = _ts(utc_now())
    project = Project(
        id=_id("project"),
        title=title,
        revision=1,
        prompt_revision=1,
        confirmed_revision=1,
        brief_status="confirmed",
        product_description="米色托特包",
        image_size="1024x1024",
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    await session.flush()
    return project


async def _seed_candidate(
    app,
    *,
    completed_delta: timedelta,
    status: str = "succeeded",
    review_status: str = "quarantined",
    relative_path: str | None = None,
    data: bytes | None = None,
    project_id: str | None = None,
    asset_project_id: str | None = None,
    asset_kind: str = "generated",
    sha256: str | None = "match",
    version_no: int = 1,
) -> Seeded:
    settings: Settings = app.state.settings
    data = data or _png((220, 200, 160))
    asset_id = _id("asset")
    generation_id = _id("generation")
    relative_path = relative_path or f"generated/{asset_id}.png"
    digest = hashlib.sha256(data).hexdigest() if sha256 == "match" else sha256
    now = utc_now()
    async with write_transaction(app.state.session_factory) as session:
        if project_id is None:
            project = await _create_project(session)
            project_id = project.id
        else:
            project = await session.get(Project, project_id)
            assert project is not None
        if asset_project_id is None:
            asset_project_id = project_id
        file_path = Path(settings.resolved_data_dir()) / relative_path
        if relative_path and not os.path.isabs(relative_path):
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
        elif relative_path and os.path.isabs(relative_path):
            file_path = Path(relative_path)
            file_path.write_bytes(data)
        asset = Asset(
            id=asset_id,
            project_id=asset_project_id,
            kind=asset_kind,
            relative_path=relative_path,
            mime_type="image/png",
            width=8,
            height=8,
            byte_size=len(data),
            sha256=digest,
            review_status=review_status,
            created_at=_ts(now),
        )
        session.add(asset)
        await session.flush()
        generation = Generation(
            id=generation_id,
            project_id=project_id,
            version_no=version_no,
            request_id=str(uuid.uuid4()),
            request_canonical="{}",
            request_digest="0" * 64,
            confirmed_revision=1,
            status=status,
            stage="done",
            brief_snapshot_json="{}",
            prompt="",
            image_size="8x8",
            adapter_id="stub-visual",
            model="stub-model",
            service_fingerprint="f" * 64,
            result_asset_id=asset_id,
            submit_attempted=0,
            created_at=_ts(now - timedelta(hours=30)),
            completed_at=_ts(now - completed_delta),
        )
        session.add(generation)
        await session.flush()
    return Seeded(project_id, generation_id, asset_id, file_path, data)


async def _seed_diagnostic(
    app,
    *,
    event: str,
    age: timedelta,
    project_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> str:
    event_id = _id("diag")
    async with write_transaction(app.state.session_factory) as session:
        session.add(
            DiagnosticEvent(
                id=event_id,
                project_id=project_id,
                event=event,
                detail_json=json.dumps(detail or {}, ensure_ascii=False),
                created_at=_ts(utc_now() - age),
            )
        )
        await session.flush()
    return event_id


def _failure_ids(report: retention.RetentionReport) -> set[str]:
    return {failure.object_id for failure in report.failures}


@pytest.mark.asyncio
async def test_candidate_boundaries_use_original_terminal_time(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        before = await _seed_candidate(app, completed_delta=timedelta(hours=23, minutes=59))
        boundary = await _seed_candidate(app, completed_delta=timedelta(hours=24), version_no=2)
        after = await _seed_candidate(app, completed_delta=timedelta(hours=25), version_no=3)
        running = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            status="running",
            version_no=4,
        )

        report = await retention.run_retention_cleanup(app, now=fixed_now)

        assert report.deleted_asset_ids == {boundary.asset_id, after.asset_id}
        assert before.path.is_file()
        assert not boundary.path.is_file()
        assert not after.path.is_file()
        assert running.path.is_file()
        assert report.failures == []

        async with app.state.session_factory() as session:
            boundary_row = await session.get(Generation, boundary.generation_id)
            after_row = await session.get(Generation, after.generation_id)
            assert boundary_row is not None and after_row is not None
            assert boundary_row.completed_at is not None
            assert boundary_row.completed_at.endswith("Z")
            assert after_row.completed_at is not None
            assert after_row.completed_at.endswith("Z")
            assert boundary_row.status == "succeeded"
            assert boundary_row.result_asset_id == boundary.asset_id


@pytest.mark.asyncio
async def test_diagnostics_expire_but_approval_evidence_is_kept(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        old_ordinary = await _seed_diagnostic(
            app,
            event="generation_progress",
            age=timedelta(days=30, seconds=1),
            detail={
                "correlation_id": "corr-old",
                "stage": "S08",
                "error_code": "X",
                "duration_ms": 3,
            },
        )
        boundary_ordinary = await _seed_diagnostic(
            app,
            event="generation_progress",
            age=timedelta(days=30),
        )
        fresh_ordinary = await _seed_diagnostic(
            app,
            event="generation_progress",
            age=timedelta(days=29),
        )
        protected_old = await _seed_diagnostic(
            app,
            event="requirements_brief_approved",
            age=timedelta(days=90),
            detail={"artifact_id": "approved-artifact"},
        )
        protected_risk_ack = await _seed_diagnostic(
            app,
            event="generation_risk_acknowledged",
            age=timedelta(days=90),
            detail={"request_id": "risk-ack"},
        )

        async with write_transaction(app.state.session_factory) as session:
            project = await _create_project(session, title="审批证据")
            run = WorkflowRun(
                id=_id("run"),
                project_id=project.id,
                sequence_no=1,
                stage="S10",
                status="active",
                requirement_revision=1,
                created_at=_ts(utc_now()),
                updated_at=_ts(utc_now()),
            )
            session.add(run)
            await session.flush()
            artifact = Artifact(
                id=_id("artifact"),
                run_id=run.id,
                project_id=project.id,
                kind="delivery_manifest",
                revision_no=1,
                status="approved",
                payload_json=json.dumps({"asset_id": "evidence-asset"}),
                digest="a" * 64,
                created_at=_ts(utc_now() - timedelta(days=90)),
            )
            session.add(artifact)
            await session.flush()
            review = ArtifactReview(
                id=_id("review"),
                artifact_id=artifact.id,
                project_id=project.id,
                reviewer_role="output_reviewer",
                target_kind="artifact",
                decision="pass",
                checks_json="[]",
                created_at=_ts(utc_now() - timedelta(days=90)),
            )
            approval = Approval(
                id=_id("approval"),
                project_id=project.id,
                artifact_id=artifact.id,
                approval_type="delivery_manifest",
                artifact_digest="a" * 64,
                approved_by="output_reviewer",
                requirement_snapshot_digest="b" * 64,
                referenced_material_digest="c" * 64,
                prompt_version="2.0.0",
                rules_version="2.0.0",
                model_fingerprint="stub",
                checks_json="[]",
                created_at=_ts(utc_now() - timedelta(days=90)),
            )
            session.add_all([review, approval])
            await session.flush()
            artifact_id = artifact.id
            review_id = review.id
            approval_id = approval.id

        report = await retention.run_retention_cleanup(app, now=fixed_now)

        assert report.deleted_diagnostic_ids == {old_ordinary, boundary_ordinary}
        async with app.state.session_factory() as session:
            remaining = set((await session.scalars(select(DiagnosticEvent.id))).all())
            assert fresh_ordinary in remaining
            assert protected_old in remaining
            assert protected_risk_ack in remaining
            assert await session.get(Artifact, artifact_id) is not None
            assert await session.get(ArtifactReview, review_id) is not None
            assert await session.get(Approval, approval_id) is not None


@pytest.mark.asyncio
async def test_cleanup_rejects_escape_and_wrong_ownership(full_settings, tmp_path, monkeypatch):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.png"
    sentinel.write_bytes(_png((1, 2, 3)))
    async with _live_app(full_settings, registry) as (app, _client):
        data_dir = Path(full_settings.resolved_data_dir())
        traversal = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            relative_path="../outside/sentinel.png",
            data=_png((1, 2, 3)),
            version_no=1,
        )
        absolute = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            relative_path=str(sentinel),
            data=_png((1, 2, 3)),
            version_no=2,
        )

        junction_root = data_dir / "generated" / "junction-target"
        junction_root.mkdir(parents=True, exist_ok=True)
        outside_junction = tmp_path / "junction-outside"
        outside_junction.mkdir()
        junction_sentinel = outside_junction / "secret.png"
        junction_sentinel.write_bytes(_png((4, 5, 6)))
        junction_path = data_dir / "generated" / "junction-link"
        if junction_path.exists():
            junction_path.rmdir()
        created = subprocess.run(  # noqa: ASYNC221 - Windows junction setup is short and synchronous
            ["cmd", "/c", "mklink", "/J", str(junction_path), str(outside_junction)],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip("当前环境无法创建目录联接，跳过联接逃逸用例")
        junction = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            relative_path="generated/junction-link/secret.png",
            data=_png((4, 5, 6)),
            version_no=3,
        )

        other_project = None
        async with write_transaction(app.state.session_factory) as session:
            other_project = await _create_project(session, title="错误归属")
            other_project_id = other_project.id
        wrong_owner = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            project_id=other_project_id,
            asset_project_id=(await _create_project_id(app)),
            version_no=1,
        )

        report = await retention.run_retention_cleanup(app, now=fixed_now)

        assert sentinel.read_bytes() == _png((1, 2, 3))
        assert junction_sentinel.read_bytes() == _png((4, 5, 6))
        assert report.deleted_asset_ids == set()
        assert traversal.asset_id in _failure_ids(report)
        assert absolute.asset_id in _failure_ids(report)
        assert junction.asset_id in _failure_ids(report)
        assert wrong_owner.asset_id in _failure_ids(report)


async def _create_project_id(app) -> str:
    async with write_transaction(app.state.session_factory) as session:
        project = await _create_project(session, title="另一项目")
        return project.id


@pytest.mark.asyncio
async def test_cleanup_preserves_published_reference_history_and_shared_dependencies(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        published = await _seed_candidate(app, completed_delta=timedelta(days=5), version_no=1)
        legacy = await _seed_candidate(
            app,
            completed_delta=timedelta(days=5),
            review_status="legacy_unreviewed",
            version_no=2,
        )
        approved = await _seed_candidate(
            app,
            completed_delta=timedelta(days=5),
            review_status="approved",
            version_no=3,
        )
        reference = await _seed_candidate(
            app,
            completed_delta=timedelta(days=5),
            review_status="quarantined",
            asset_kind="reference",
            version_no=4,
        )
        artifact_bound = await _seed_candidate(
            app, completed_delta=timedelta(days=5), project_id=published.project_id, version_no=5
        )
        approval_bound = await _seed_candidate(
            app, completed_delta=timedelta(days=5), project_id=published.project_id, version_no=6
        )
        shared_path = await _seed_candidate(app, completed_delta=timedelta(days=5), version_no=7)
        shared_existing = await _seed_candidate(
            app,
            completed_delta=timedelta(days=5),
            review_status="approved",
            version_no=8,
        )
        # 第二个资产复用同一路径，模拟共享文件；任何一方存在有效依赖时都不能按单行删除。
        async with write_transaction(app.state.session_factory) as session:
            shared = await session.get(Asset, shared_existing.asset_id)
            assert shared is not None
            shared.relative_path = shared_path.path.relative_to(
                full_settings.resolved_data_dir()
            ).as_posix()

        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, published.project_id)
            assert project is not None
            project.reference_asset_id = reference.asset_id
            run = WorkflowRun(
                id=_id("run"),
                project_id=project.id,
                sequence_no=1,
                stage="S10",
                status="active",
                requirement_revision=1,
                created_at=_ts(utc_now()),
                updated_at=_ts(utc_now()),
            )
            session.add(run)
            await session.flush()
            artifact = Artifact(
                id=_id("artifact"),
                run_id=run.id,
                project_id=project.id,
                kind="delivery_manifest",
                revision_no=1,
                status="approved",
                payload_json=json.dumps({"asset_id": artifact_bound.asset_id}),
                digest="a" * 64,
                created_at=_ts(utc_now()),
            )
            session.add(artifact)
            await session.flush()
            job = GenerationJob(
                id=_id("job"),
                project_id=project.id,
                run_id=run.id,
                generation_id=published.generation_id,
                request_id=str(uuid.uuid4()),
                request_digest="d" * 64,
                prompt_artifact_id=None,
                confirmed_snapshot_digest="e" * 64,
                status="published",
                submit_attempted=1,
                result_asset_id=published.asset_id,
                created_at=_ts(utc_now()),
                updated_at=_ts(utc_now()),
            )
            session.add(job)
            await session.flush()
            delivery = Delivery(
                id=_id("delivery"),
                project_id=project.id,
                generation_job_id=job.id,
                asset_id=published.asset_id,
                manifest_artifact_id=artifact.id,
                status="published",
                filename="published.png",
                caption="已发布",
                asset_digest=hashlib.sha256(published.data).hexdigest(),
                approved_at=_ts(utc_now()),
                published_at=_ts(utc_now()),
                created_at=_ts(utc_now()),
            )
            approval = Approval(
                id=_id("approval"),
                project_id=project.id,
                artifact_id=artifact.id,
                approval_type="image",
                artifact_digest="a" * 64,
                approved_by="output_reviewer",
                requirement_snapshot_digest="e" * 64,
                referenced_material_digest="c" * 64,
                prompt_version="2.0.0",
                rules_version="2.0.0",
                model_fingerprint="stub",
                checks_json=json.dumps({"asset_id": approval_bound.asset_id}),
                created_at=_ts(utc_now()),
            )
            legacy_item = LegacyItem(
                id=_id("legacy"),
                project_id=project.id,
                kind="asset",
                ref_id=legacy.asset_id,
                status="legacy_unreviewed",
                created_at=_ts(utc_now()),
            )
            session.add_all([delivery, approval, legacy_item])
            await session.flush()

        report = await retention.run_retention_cleanup(app, now=fixed_now)
        for item in (
            published,
            legacy,
            approved,
            reference,
            artifact_bound,
            approval_bound,
            shared_path,
        ):
            assert item.path.is_file(), item.asset_id
        assert report.deleted_asset_ids == set()


@pytest.mark.asyncio
async def test_rejected_quarantined_candidates_expire_while_approved_dependencies_are_kept(
    full_settings,
):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        rejected_image = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            status="failed",
            review_status="rejected",
            version_no=1,
        )
        rejected_artifact = await _seed_candidate(
            app, completed_delta=timedelta(hours=25), version_no=2
        )
        quarantined_job = await _seed_candidate(
            app, completed_delta=timedelta(hours=25), version_no=3
        )
        approved_image = await _seed_candidate(
            app,
            completed_delta=timedelta(hours=25),
            review_status="approved",
            version_no=4,
        )

        async with write_transaction(app.state.session_factory) as session:
            run = WorkflowRun(
                id=_id("run"),
                project_id=rejected_artifact.project_id,
                sequence_no=1,
                stage="S10",
                status="active",
                requirement_revision=1,
                created_at=_ts(utc_now()),
                updated_at=_ts(utc_now()),
            )
            session.add(run)
            await session.flush()
            artifact = Artifact(
                id=_id("artifact"),
                run_id=run.id,
                project_id=rejected_artifact.project_id,
                kind="quality_report",
                revision_no=1,
                status="rejected",
                payload_json=json.dumps({"asset_id": rejected_artifact.asset_id}),
                digest="b" * 64,
                created_at=_ts(utc_now()),
            )
            session.add(artifact)
            await session.flush()
            review = ArtifactReview(
                id=_id("review"),
                artifact_id=artifact.id,
                project_id=rejected_artifact.project_id,
                reviewer_role="quality_compliance",
                target_kind="image",
                decision="reject",
                checks_json=json.dumps([{"rule_id": "G06_IMAGE", "outcome": "fail"}]),
                raw_json=json.dumps({"asset_id": rejected_artifact.asset_id}),
                created_at=_ts(utc_now()),
            )
            session.add(review)
            await session.flush()

            job_run = WorkflowRun(
                id=_id("run"),
                project_id=quarantined_job.project_id,
                sequence_no=1,
                stage="S08",
                status="active",
                requirement_revision=1,
                created_at=_ts(utc_now()),
                updated_at=_ts(utc_now()),
            )
            session.add(job_run)
            await session.flush()
            job = GenerationJob(
                id=_id("job"),
                project_id=quarantined_job.project_id,
                run_id=job_run.id,
                generation_id=quarantined_job.generation_id,
                request_id=str(uuid.uuid4()),
                request_digest="7" * 64,
                prompt_artifact_id=None,
                confirmed_snapshot_digest="8" * 64,
                status="quarantined",
                submit_attempted=1,
                result_asset_id=quarantined_job.asset_id,
                created_at=_ts(utc_now()),
                updated_at=_ts(utc_now()),
            )
            session.add(job)
            await session.flush()

            # 报告获批只允许展示报告，不能把未通过的候选图变成永久保留。
            approved_failed_report = Artifact(
                id=_id("artifact"),
                run_id=job_run.id,
                project_id=quarantined_job.project_id,
                kind="quality_report",
                revision_no=1,
                status="approved",
                payload_json=json.dumps(
                    {"asset_id": quarantined_job.asset_id, "decision": "fail"}
                ),
                digest="c" * 64,
                created_at=_ts(utc_now()),
            )
            session.add(approved_failed_report)
            await session.flush()

        report = await retention.run_retention_cleanup(app, now=fixed_now)

        # 质检拒绝、被拒产物引用、quarantined 终态候选都属于未发布候选，满 24h 清理。
        for item in (rejected_image, rejected_artifact, quarantined_job):
            assert item.asset_id in report.deleted_asset_ids, item.asset_id
            assert not item.path.is_file(), item.asset_id
        # 已批准图片是有效证据，必须保留文件。
        assert approved_image.asset_id not in report.deleted_asset_ids
        assert approved_image.path.is_file()


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_and_does_not_delete_adjacent_files(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        expired = await _seed_candidate(app, completed_delta=timedelta(hours=25), version_no=1)
        fresh = await _seed_candidate(app, completed_delta=timedelta(hours=23), version_no=2)
        adjacent = fresh.path.parent / "adjacent.png"
        adjacent.write_bytes(_png((9, 9, 9)))

        first = await retention.run_retention_cleanup(app, now=fixed_now)
        second = await retention.run_retention_cleanup(app, now=fixed_now + timedelta(minutes=5))

        assert expired.asset_id in first.deleted_asset_ids
        assert second.deleted_asset_ids == set()
        assert second.failures == []
        assert fresh.path.is_file()
        assert adjacent.is_file()
        assert not expired.path.is_file()


@pytest.mark.asyncio
async def test_failed_delete_keeps_file_and_records_redacted_error(full_settings, monkeypatch):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        candidate = await _seed_candidate(app, completed_delta=timedelta(hours=25))

        def fail_unlink(_path: Path) -> None:
            raise OSError("disk failure with C:/secret/path")

        monkeypatch.setattr(retention, "_unlink_candidate", fail_unlink)
        report = await retention.run_retention_cleanup(app, now=fixed_now)

        assert report.deleted_asset_ids == set()
        assert candidate.asset_id in _failure_ids(report)
        assert candidate.path.is_file()
        async with app.state.session_factory() as session:
            events = list(
                (
                    await session.scalars(
                        select(DiagnosticEvent).where(
                            DiagnosticEvent.event == "retention_cleanup_failed"
                        )
                    )
                ).all()
            )
            assert events
            detail = json.loads(events[-1].detail_json)
            assert set(detail) == {"correlation_id", "stage", "error_code", "duration_ms"}
            assert "C:/secret/path" not in events[-1].detail_json
            assert report.failures[0].error_code == "FILE_DELETE_FAILED"


@pytest.mark.asyncio
async def test_public_http_exposes_expired_state_and_never_serves_candidate_bytes(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, client):
        candidate = await _seed_candidate(app, completed_delta=timedelta(hours=25))

        detail_before = await client.get(f"/api/projects/{candidate.project_id}")
        assert detail_before.status_code == 200, detail_before.text
        body_before = detail_before.json()
        generation_before = body_before["generations"][0]
        assert generation_before["retention_status"] == "expired"
        assert generation_before["retention_expires_at"] is not None
        assert generation_before["can_retrieve_result"] is False

        asset_response = await client.get(f"/api/assets/{candidate.asset_id}")
        assert asset_response.status_code == 409
        assert candidate.data not in asset_response.content
        download_response = await client.get(f"/api/assets/{candidate.asset_id}?download=true")
        assert download_response.status_code == 409

        report = await retention.run_retention_cleanup(app, now=fixed_now)
        assert candidate.asset_id in report.deleted_asset_ids

        detail_after = await client.get(f"/api/projects/{candidate.project_id}")
        assert detail_after.status_code == 200, detail_after.text
        generation_after = detail_after.json()["generations"][0]
        assert generation_after["retention_status"] == "expired"
        assert generation_after["result_asset"]["availability"] == "missing"
        assert generation_after["result_asset_id"] == candidate.asset_id
        assert generation_after["can_retrieve_result"] is False

        one = await client.get(f"/api/generations/{candidate.generation_id}")
        assert one.status_code == 200
        assert one.json()["retention_status"] == "expired"
        missing = await client.get(f"/api/assets/{candidate.asset_id}")
        assert missing.status_code == 409


@pytest.mark.asyncio
async def test_failed_and_unknown_terminals_expire_but_missing_terminal_time_never_guessed(
    full_settings,
):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        failed = await _seed_candidate(
            app, completed_delta=timedelta(hours=25), status="failed", version_no=1
        )
        unknown = await _seed_candidate(
            app, completed_delta=timedelta(hours=25), status="unknown", version_no=2
        )
        missing_time = await _seed_candidate(
            app, completed_delta=timedelta(hours=25), status="failed", version_no=3
        )
        async with write_transaction(app.state.session_factory) as session:
            generation = await session.get(Generation, missing_time.generation_id)
            assert generation is not None
            generation.completed_at = None

        report = await retention.run_retention_cleanup(app, now=fixed_now)

        # s08 的失败/未知终态同样是终态，完成时间存在时按原终止时间清理候选。
        assert failed.asset_id in report.deleted_asset_ids
        assert unknown.asset_id in report.deleted_asset_ids
        assert not failed.path.is_file()
        assert not unknown.path.is_file()
        # 终态但完成时间为空：绝不猜测、绝不删除，只记录固定错误码。
        assert missing_time.asset_id not in report.deleted_asset_ids
        assert missing_time.path.is_file()
        failure = next(
            item for item in report.failures if item.object_id == missing_time.generation_id
        )
        assert failure.error_code == "RETENTION_TERMINAL_TIME_MISSING"
        async with app.state.session_factory() as session:
            skipped = list(
                (
                    await session.scalars(
                        select(DiagnosticEvent).where(
                            DiagnosticEvent.event == "retention_cleanup_skipped"
                        )
                    )
                ).all()
            )
        assert any(
            json.loads(event.detail_json).get("error_code") == "RETENTION_TERMINAL_TIME_MISSING"
            for event in skipped
        )


@pytest.mark.asyncio
async def test_cleanup_never_calls_models_retrieval_or_generation(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    registry = build_test_registry(llm, visual)
    fixed_now = utc_now() + timedelta(seconds=2)
    async with _live_app(full_settings, registry) as (app, _client):
        await _seed_candidate(app, completed_delta=timedelta(hours=25))
        await retention.run_retention_cleanup(app, now=fixed_now)
        assert llm.role_calls_received == []
        assert llm.calls == []
        assert visual.submit_calls == []
        assert visual.fetch_calls == []
        assert visual.poll_calls == []
        assert registry.recorder.llm_builds == 0
        assert registry.recorder.visual_builds == 0


def _t20_process_request(port: int, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _start_t20_server(settings: Settings, port: int, call_log: Path) -> subprocess.Popen[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_", "T20_"))
    }
    env.update(
        DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        PYTHONPATH=os.pathsep.join((str(MAIN_BACKEND_DIR), str(BACKEND_DIR))),
        PYTHONUNBUFFERED="1",
        LLM_ADAPTER="stub-llm",
        LLM_BASE_URL="https://llm.invalid",
        LLM_API_KEY="sk-test-t20",
        LLM_MODEL="stub-model",
        LLM_VISION_MODEL="stub-vision",
        LLM_TIMEOUT_SECONDS="5",
        VISUAL_ADAPTER="stub-visual",
        VISUAL_BASE_URL="https://visual.invalid",
        VISUAL_API_KEY="sk-test-t20",
        VISUAL_MODEL="stub-model",
        VISUAL_IMAGE_SIZE="1024x1024",
        VISUAL_TIMEOUT_SECONDS="5",
        T20_CALL_LOG=str(call_log),
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.t20_retention_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@pytest.mark.asyncio
async def test_real_process_restart_cleans_during_downtime_without_adapter_calls(full_settings):
    from tests.test_t01_process import free_port, stop_server, wait_for_health

    call_log = Path(full_settings.data_dir).parent / "t20-calls.log"
    call_log.unlink(missing_ok=True)
    registry = build_test_registry(LLMStub(), VisualStub())
    async with _live_app(full_settings, registry) as (app, _client):
        expired = await _seed_candidate(app, completed_delta=timedelta(hours=25))
        kept = await _seed_candidate(app, completed_delta=timedelta(hours=23), version_no=2)

    port = free_port()
    server = _start_t20_server(full_settings, port, call_log)
    try:
        wait_for_health(server, port)
        # 停机期间满期的候选在下次启动时清理；未满期对象保持原文件。
        assert not expired.path.is_file()
        assert kept.path.is_file()

        status, generation = _t20_process_request(
            port, "GET", f"/api/generations/{expired.generation_id}"
        )
        assert status == 200, generation
        assert generation["retention_status"] == "expired"
        assert generation["result_asset"]["availability"] == "missing"
        assert generation["can_retrieve_result"] is False
        status, missing = _t20_process_request(port, "GET", f"/api/assets/{expired.asset_id}")
        assert status == 409, missing
        status, detail = _t20_process_request(port, "GET", f"/api/projects/{expired.project_id}")
        assert status == 200, detail
        assert detail["generations"][0]["retention_status"] == "expired"
    finally:
        stop_server(server)

    # 启动清理只操作本地持久状态，绝不能调用任何模型、重取原结果或生图。
    assert not call_log.exists() or call_log.read_text(encoding="utf-8") == ""
