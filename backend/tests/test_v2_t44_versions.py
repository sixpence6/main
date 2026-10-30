from __future__ import annotations

import asyncio
import hashlib
import io
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.main import create_app
from app.models import Asset, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow.contracts import digest_payload
from app.workflow.tables import (
    Approval,
    Artifact,
    ArtifactReview,
    Delivery,
    GenerationJob,
    WorkflowRun,
)
from tests.conftest import migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry


def _png_bytes(color: tuple[int, int, int] = (20, 80, 160), size: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _brief_payload(*, product: str = "米色托特包") -> dict[str, Any]:
    return {
        "product_description": product,
        "purpose": "电商主图",
        "facts": [],
        "creative_direction": "简洁棚拍",
        "style_keywords": ["简洁"],
        "composition": "正面居中",
        "lighting": "柔光",
        "texts": [],
        "must_include": ["一只托特包"],
        "must_preserve": ["米色"],
        "forbidden": ["额外文字"],
        "size": "8x8",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": [],
        "unresolved": [],
    }


def _prompt_payload(*, asset_id: str | None = None) -> dict[str, Any]:
    return {
        "positive_prompt": "米色托特包，正面居中，简洁棚拍，柔光",
        "negative_prompt": "额外文字",
        "size": "8x8",
        "count": 1,
        "reference_asset_id": asset_id,
        "requirement_coverage": [
            {"rule_id": "hard:subject", "binding": "positive_prompt", "value": "米色托特包"}
        ],
    }


def _quality_payload(asset_id: str) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "decision": "pass",
        "checks": [
            {
                "rule_id": "hard:subject",
                "outcome": "pass",
                "evidence_ids": [asset_id],
                "note": "目标主体可见",
            }
        ],
        "soft_feedback": [],
        "limitations": [],
        "user_summary": "未发现明确偏差。",
    }


def _manifest_payload(asset_id: str, revision: int) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "requirement_revision": revision,
        "filename_hint": "bag-main.png",
        "caption": "已按当前需求完成检查。",
    }


def _reference_payload(asset_id: str) -> dict[str, Any]:
    return {
        "reference_usage": "product",
        "observations": [
            {
                "kind": "color",
                "description": "参考图主体为米色",
                "confidence": "clear",
                "evidence_ids": ["reference:" + asset_id],
            }
        ],
        "texts": [],
        "uncertainties": [],
    }


@dataclass
class Seed:
    project_id: str
    run_id: str
    job_id: str
    asset_id: str
    asset_bytes: bytes
    digest: str
    manifest_id: str
    final_brief_id: str
    prompt_id: str
    reference_asset_id: str | None
    reference_report_id: str | None
    requirement_digest: str


async def _seed_published(
    settings: Any,
    session: Any,
    *,
    with_reference: bool = False,
    job_status: str = "published",
    delivery_status: str = "published",
) -> Seed:
    project_id = str(uuid4())
    run_id = str(uuid4())
    job_id = str(uuid4())
    asset_id = str(uuid4())
    manifest_id = str(uuid4())
    final_brief_id = str(uuid4())
    prompt_id = str(uuid4())
    quality_id = str(uuid4())
    now = format_timestamp(utc_now())
    asset_bytes = _png_bytes()
    digest = hashlib.sha256(asset_bytes).hexdigest()
    relative_path = f"generated/{asset_id}.png"
    target = settings.resolved_data_dir() / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(asset_bytes)

    project = Project(
        id=project_id,
        title="T14 历史版本项目",
        revision=1,
        prompt_revision=1,
        confirmed_revision=1,
        brief_status="confirmed",
        image_size="8x8",
        prompt="米色托特包，正面居中",
        created_at=now,
        updated_at=now,
        reference_asset_id=None,
    )
    session.add(project)
    await session.flush()
    session.add(
        WorkflowRun(
            id=run_id,
            project_id=project_id,
            sequence_no=1,
            stage="S10",
            status="completed",
            requirement_revision=1,
            issue_code="DELIVERY_PUBLISHED",
            issue_message="交付已发布。",
            template_id="WAIT_STAGE",
            template_params_json=json.dumps({"stage_label": "准备交付"}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    reference_asset_id = None
    reference_report_id = None
    if with_reference:
        reference_asset_id = str(uuid4())
        reference_bytes = _png_bytes((180, 150, 100), 9)
        ref_relative = f"references/{reference_asset_id}.png"
        ref_target = settings.resolved_data_dir() / ref_relative
        ref_target.parent.mkdir(parents=True, exist_ok=True)
        ref_target.write_bytes(reference_bytes)
        session.add(
            Asset(
                id=reference_asset_id,
                project_id=project_id,
                kind="reference",
                relative_path=ref_relative,
                mime_type="image/png",
                width=9,
                height=9,
                byte_size=len(reference_bytes),
                sha256=hashlib.sha256(reference_bytes).hexdigest(),
                review_status="approved",
                created_at=now,
            )
        )
        await session.flush()
        project.reference_asset_id = reference_asset_id
        reference_payload = _reference_payload(reference_asset_id)
        reference_report_id = str(uuid4())
        session.add(
            Artifact(
                id=reference_report_id,
                run_id=run_id,
                project_id=project_id,
                kind="reference_report",
                revision_no=1,
                status="approved",
                payload_json=json.dumps(reference_payload, ensure_ascii=False, sort_keys=True),
                digest=digest_payload(reference_payload),
                created_at=now,
            )
        )
        session.add(
            ArtifactReview(
                id=str(uuid4()),
                artifact_id=reference_report_id,
                project_id=project_id,
                reviewer_role="output_reviewer",
                target_kind="artifact",
                decision="pass",
                checks_json=json.dumps({"checks": []}, ensure_ascii=False, sort_keys=True),
                raw_json=None,
                created_at=now,
            )
        )

    brief = _brief_payload()
    brief["reference_asset_id"] = reference_asset_id
    brief["reference_usage"] = "product" if reference_asset_id else "none"
    prompt = _prompt_payload(asset_id=reference_asset_id)
    quality = _quality_payload(asset_id)
    manifest = _manifest_payload(asset_id, 1)
    requirement_digest = digest_payload(brief)

    session.add(
        Asset(
            id=asset_id,
            project_id=project_id,
            kind="generated",
            relative_path=relative_path,
            mime_type="image/png",
            width=8,
            height=8,
            byte_size=len(asset_bytes),
            sha256=digest,
            review_status="approved",
            created_at=now,
        )
    )
    await session.flush()
    for artifact_id, kind, payload in (
        (final_brief_id, "final_brief", brief),
        (prompt_id, "prompt_plan", prompt),
        (quality_id, "quality_report", quality),
        (manifest_id, "delivery_manifest", manifest),
    ):
        session.add(
            Artifact(
                id=artifact_id,
                run_id=run_id,
                project_id=project_id,
                kind=kind,
                revision_no=1,
                status="approved",
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                digest=digest_payload(payload),
                created_at=now,
            )
        )
    await session.flush()
    session.add(
        ArtifactReview(
            id=str(uuid4()),
            artifact_id=manifest_id,
            project_id=project_id,
            reviewer_role="output_reviewer",
            target_kind="artifact",
            decision="pass",
            checks_json=json.dumps({"checks": []}, ensure_ascii=False, sort_keys=True),
            raw_json=None,
            created_at=now,
        )
    )

    async def approval(approval_type: str, artifact_id: str, artifact_digest: str) -> None:
        session.add(
            Approval(
                id=str(uuid4()),
                project_id=project_id,
                artifact_id=artifact_id,
                approval_type=approval_type,
                artifact_digest=artifact_digest,
                approved_by="output_reviewer" if approval_type != "image" else "user",
                requirement_snapshot_digest=requirement_digest,
                referenced_material_digest=digest_payload(
                    {"reference": reference_asset_id or "none"}
                ),
                prompt_version="2.0.0",
                rules_version="2.0.0",
                model_fingerprint="stub:model:deadbeef",
                checks_json=json.dumps(
                    (
                        {"asset_id": asset_id, "asset_digest": digest}
                        if approval_type == "image"
                        else {}
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                created_at=now,
            )
        )

    await approval("requirements_card", final_brief_id, digest_payload(brief))
    await approval("generation_prompt", prompt_id, digest_payload(prompt))
    await approval("image", quality_id, digest_payload(quality))
    await approval("delivery_manifest", manifest_id, digest_payload(manifest))
    session.add(
        GenerationJob(
            id=job_id,
            project_id=project_id,
            run_id=run_id,
            generation_id=None,
            request_id=str(uuid4()),
            request_digest="request-digest",
            prompt_artifact_id=prompt_id,
            confirmed_snapshot_digest=requirement_digest,
            status=job_status,
            submit_attempted=1,
            remote_task_id="remote-task-not-public",
            result_asset_id=asset_id,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    session.add(
        Delivery(
            id=str(uuid4()),
            project_id=project_id,
            generation_job_id=job_id,
            asset_id=asset_id,
            manifest_artifact_id=manifest_id,
            status=delivery_status,
            filename="bag-main.png",
            caption="已按当前需求完成检查。",
            asset_digest=digest,
            approved_at=now,
            published_at=now if delivery_status == "published" else None,
            created_at=now,
        )
    )
    await session.flush()
    return Seed(
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
        asset_id=asset_id,
        asset_bytes=asset_bytes,
        digest=digest,
        manifest_id=manifest_id,
        final_brief_id=final_brief_id,
        prompt_id=prompt_id,
        reference_asset_id=reference_asset_id,
        reference_report_id=reference_report_id,
        requirement_digest=requirement_digest,
    )


@asynccontextmanager
async def _env(
    settings: Any,
    *,
    with_reference: bool = False,
    job_status: str = "published",
    delivery_status: str = "published",
    stub: LLMStub | None = None,
    visual: VisualStub | None = None,
) -> AsyncIterator[tuple[Any, AsyncClient, Seed, LLMStub, VisualStub]]:
    stub = stub or LLMStub()
    visual = visual or VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with application.state.session_factory() as session:
            seed = await _seed_published(
                settings,
                session,
                with_reference=with_reference,
                job_status=job_status,
                delivery_status=delivery_status,
            )
            await session.commit()
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield application, client, seed, stub, visual


async def _post(
    client: AsyncClient,
    project_id: str,
    action: str,
    *,
    revision: int,
    text: str | None = None,
    payload: dict[str, Any] | None = None,
):
    body: dict[str, Any] = {"expected_revision": revision}
    if text is not None:
        body["text"] = text
    if payload is not None:
        body["payload"] = payload
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/{action}", json=body
    )


@pytest.mark.asyncio
async def test_request_new_version_creates_fresh_run_and_preserves_published_history(full_settings):
    async with _env(full_settings) as (application, client, seed, stub, visual):
        response = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="更换更简洁的背景",
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "active"
        assert view["run_id"] != seed.run_id
        assert view["project_revision"] == 2
        assert view["requirement_revision"] == 2
        assert [action["id"] for action in view["available_actions"]] == ["compose_brief"]
        assert stub.role_calls_received == []
        assert visual.submit_calls == []

        async with application.state.session_factory() as session:
            old_run = await session.get(WorkflowRun, seed.run_id)
            assert old_run is not None
            assert (old_run.stage, old_run.status) == ("S10", "completed")
            old_delivery = await session.scalar(
                select(Delivery).where(Delivery.project_id == seed.project_id)
            )
            assert old_delivery is not None and old_delivery.status == "published"
            old_asset = await session.get(Asset, seed.asset_id)
            assert old_asset is not None and old_asset.relative_path.endswith(".png")
            project = await session.get(Project, seed.project_id)
            assert project is not None
            assert project.prompt_revision is None and project.confirmed_revision is None
            assert project.brief_status == "draft"
        old_preview = await client.get(f"/api/assets/{seed.asset_id}")
        assert old_preview.status_code == 200
        assert old_preview.content == seed.asset_bytes
        old_download = await client.get(f"/api/assets/{seed.asset_id}?download=true")
        assert old_download.status_code == 200
        assert old_download.content == seed.asset_bytes

        async with application.state.session_factory() as session:
            approvals = (
                await session.scalars(
                    select(Approval).where(Approval.project_id == seed.project_id)
                )
            ).all()
        by_type = {approval.approval_type: approval for approval in approvals}
        assert by_type["image"].revoked_at is None
        assert by_type["delivery_manifest"].revoked_at is None
        assert by_type["requirements_card"].revoked_at is not None
        assert by_type["generation_prompt"].revoked_at is not None


@pytest.mark.asyncio
async def test_versions_view_contains_only_published_approved_snapshots(full_settings):
    async with _env(full_settings) as (_, client, seed, _, _):
        view = (await client.get(f"/api/projects/{seed.project_id}/workflow")).json()
        versions = view.get("versions") or []
        assert len(versions) == 1
        version = versions[0]
        assert version["run_id"] == seed.run_id
        assert version["status"] == "published"
        assert version["asset_id"] == seed.asset_id
        assert version["filename"] == "bag-main.png"
        assert version["can_download"] is True
        assert version["brief"]["product_description"] == "米色托特包"
        assert version["prompt"]["positive_prompt"].startswith("米色托特包")


@pytest.mark.asyncio
async def test_request_new_version_requires_an_approved_or_published_image(full_settings):
    async with _env(full_settings, delivery_status="revoked") as (application, client, seed, _, _):
        response = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="换背景",
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] in {"ACTION_NOT_AVAILABLE", "IMAGE_NOT_APPROVED"}
        async with application.state.session_factory() as session:
            runs = (await session.scalars(select(WorkflowRun))).all()
        assert len(runs) == 1


@pytest.mark.asyncio
async def test_stale_revision_does_not_create_a_new_version(full_settings):
    async with _env(full_settings) as (application, client, seed, _, _):
        response = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=0,
            text="换背景",
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"
        async with application.state.session_factory() as session:
            runs = (await session.scalars(select(WorkflowRun))).all()
        assert len(runs) == 1


@pytest.mark.asyncio
async def test_new_version_carries_reference_file_and_previous_approved_brief(full_settings):
    async with _env(full_settings, with_reference=True) as (application, client, seed, stub, _):
        response = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="更换更简洁的背景",
        )
        assert response.status_code == 200, response.text
        view = response.json()
        new_run_id = view["run_id"]

        async with application.state.session_factory() as session:
            new_run = await session.get(WorkflowRun, new_run_id)
            assert new_run is not None and new_run.sequence_no == 2
            copied = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == new_run_id,
                    Artifact.kind == "reference_report",
                    Artifact.status == "approved",
                )
            )
            assert copied is not None
            payload = json.loads(copied.payload_json)
            assert payload["observations"][0]["evidence_ids"] == [
                "reference:" + seed.reference_asset_id
            ]
            reference_asset = await session.get(Asset, seed.reference_asset_id)
            assert reference_asset is not None
            assert (full_settings.resolved_data_dir() / reference_asset.relative_path).is_file()
            old_run = await session.get(WorkflowRun, seed.run_id)
            assert old_run is not None and old_run.status == "completed"

        # The next S03 call must receive the old approved card and the carried
        # report as approved inputs, even though this is a new run.
        def responder(request: Any) -> dict[str, Any]:
            task = request.payload["task"]
            if task["role"] == "requirements_consultant":
                approved = request.payload["approved_inputs"]
                assert any(item.get("kind") == "final_brief" for item in approved)
                assert any(item.get("kind") == "reference_report" for item in approved)
                payload = _brief_payload()
                payload["reference_asset_id"] = seed.reference_asset_id
                payload["reference_usage"] = "product"
                return {
                    "role": "requirements_consultant",
                    "status": "ready",
                    "data": payload,
                    "issues": [],
                    "questions": [],
                }
            if task["role"] == "output_reviewer":
                target = request.payload["review_targets"][0]
                checks = [
                    {
                        "rule_id": rule,
                        "outcome": "pass",
                        "evidence_ids": [target["target_id"]],
                        "note": "",
                    }
                    for rule in (
                        "G03_STRUCTURE",
                        "G03_SUBJECT_PURPOSE",
                        "G03_SOURCE_BASIS",
                        "G03_TEXTS",
                        "G03_CONSTRAINTS",
                        "G03_SIZE",
                        "G03_UNRESOLVED",
                    )
                ]
                return {
                    "role": "output_reviewer",
                    "status": "ready",
                    "data": {
                        "results": [
                            {
                                "target_id": target["target_id"],
                                "decision": "pass",
                                "checks": checks,
                            }
                        ]
                    },
                    "issues": [],
                    "questions": [],
                }
            raise AssertionError(f"unexpected role {task['role']}")

        stub.role_responder = responder
        composed = await _post(
            client,
            seed.project_id,
            "compose_brief",
            revision=2,
            payload={},
        )
        assert composed.status_code == 200, composed.text


@pytest.mark.asyncio
async def test_two_published_versions_keep_each_other_downloadable(full_settings):
    async with _env(full_settings) as (application, client, seed, _, _):
        # Create a second published version in the same project after the first.
        async with application.state.session_factory() as session:
            now = format_timestamp(utc_now())
            second_run_id = str(uuid4())
            second_job_id = str(uuid4())
            second_asset_id = str(uuid4())
            second_manifest_id = str(uuid4())
            second_bytes = _png_bytes((10, 200, 10))
            second_digest = hashlib.sha256(second_bytes).hexdigest()
            relative = f"generated/{second_asset_id}.png"
            target = full_settings.resolved_data_dir() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(second_bytes)
            session.add(
                WorkflowRun(
                    id=second_run_id,
                    project_id=seed.project_id,
                    sequence_no=2,
                    stage="S10",
                    status="completed",
                    requirement_revision=2,
                    issue_code="DELIVERY_PUBLISHED",
                    issue_message="交付已发布。",
                    template_id="WAIT_STAGE",
                    template_params_json=json.dumps({"stage_label": "准备交付"}),
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                Asset(
                    id=second_asset_id,
                    project_id=seed.project_id,
                    kind="generated",
                    relative_path=relative,
                    mime_type="image/png",
                    width=8,
                    height=8,
                    byte_size=len(second_bytes),
                    sha256=second_digest,
                    review_status="approved",
                    created_at=now,
                )
            )
            payload = _quality_payload(second_asset_id)
            artifact_id = str(uuid4())
            session.add(
                Artifact(
                    id=artifact_id,
                    run_id=second_run_id,
                    project_id=seed.project_id,
                    kind="quality_report",
                    revision_no=1,
                    status="approved",
                    payload_json=json.dumps(payload),
                    digest=digest_payload(payload),
                    created_at=now,
                )
            )
            await session.flush()
            manifest_payload = _manifest_payload(second_asset_id, 2)
            session.add(
                Artifact(
                    id=second_manifest_id,
                    run_id=second_run_id,
                    project_id=seed.project_id,
                    kind="delivery_manifest",
                    revision_no=1,
                    status="approved",
                    payload_json=json.dumps(manifest_payload),
                    digest=digest_payload(manifest_payload),
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                Approval(
                    id=str(uuid4()),
                    project_id=seed.project_id,
                    artifact_id=second_manifest_id,
                    approval_type="delivery_manifest",
                    artifact_digest=digest_payload(manifest_payload),
                    approved_by="output_reviewer",
                    requirement_snapshot_digest="second-requirement",
                    referenced_material_digest="second-material",
                    prompt_version="2.0.0",
                    rules_version="2.0.0",
                    model_fingerprint="stub:model:deadbeef",
                    checks_json="{}",
                    created_at=now,
                )
            )
            session.add(
                Approval(
                    id=str(uuid4()),
                    project_id=seed.project_id,
                    artifact_id=artifact_id,
                    approval_type="image",
                    artifact_digest=digest_payload(payload),
                    approved_by="user",
                    requirement_snapshot_digest="second-requirement",
                    referenced_material_digest="second-material",
                    prompt_version="2.0.0",
                    rules_version="2.0.0",
                    model_fingerprint="stub:model:deadbeef",
                    checks_json=json.dumps(
                        {"asset_id": second_asset_id, "asset_digest": second_digest}
                    ),
                    created_at=now,
                )
            )
            session.add(
                GenerationJob(
                    id=second_job_id,
                    project_id=seed.project_id,
                    run_id=second_run_id,
                    generation_id=None,
                    request_id=str(uuid4()),
                    request_digest="second-request",
                    prompt_artifact_id=None,
                    confirmed_snapshot_digest="second-requirement",
                    status="published",
                    submit_attempted=1,
                    result_asset_id=second_asset_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()
            session.add(
                Delivery(
                    id=str(uuid4()),
                    project_id=seed.project_id,
                    generation_job_id=second_job_id,
                    asset_id=second_asset_id,
                    manifest_artifact_id=second_manifest_id,
                    status="published",
                    filename="second.png",
                    caption="第二版",
                    asset_digest=second_digest,
                    approved_at=now,
                    published_at=now,
                    created_at=now,
                )
            )
            await session.commit()

        first = await client.get(f"/api/assets/{seed.asset_id}?download=true")
        assert first.status_code == 200, first.text
        assert first.content == seed.asset_bytes
        second = await client.get(f"/api/assets/{second_asset_id}?download=true")
        assert second.status_code == 200, second.text
        assert second.content == second_bytes


@pytest.mark.asyncio
async def test_approved_but_unpublished_version_can_start_feedback_but_not_history(full_settings):
    async with _env(
        full_settings, job_status="approved", delivery_status="approved"
    ) as (_, client, seed, stub, visual):
        view = (await client.get(f"/api/projects/{seed.project_id}/workflow")).json()
        assert view.get("versions") == []
        response = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="再做一个浅色背景版本",
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S03"
        assert stub.role_calls_received == []
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_request_new_version_rejects_blank_or_extra_feedback(full_settings):
    async with _env(full_settings) as (application, client, seed, _, _):
        blank = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="   ",
        )
        assert blank.status_code == 422
        extra = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            payload={"text": "换背景", "force": True},
        )
        assert extra.status_code == 422
        async with application.state.session_factory() as session:
            runs = (await session.scalars(select(WorkflowRun))).all()
        assert len(runs) == 1


@pytest.mark.asyncio
async def test_replacing_reference_after_publish_keeps_completed_run_and_old_file(full_settings):
    async with _env(full_settings, with_reference=True) as (application, client, seed, stub, _):
        async with application.state.session_factory() as session:
            old_reference = await session.get(Asset, seed.reference_asset_id)
            assert old_reference is not None
            old_path = full_settings.resolved_data_dir() / old_reference.relative_path
            old_bytes = old_path.read_bytes()

        new_bytes = _png_bytes((30, 160, 60), 7)
        upload = await client.post(
            f"/api/projects/{seed.project_id}/reference",
            files={"file": ("new-reference.png", new_bytes, "image/png")},
            data={"expected_revision": "1"},
        )
        assert upload.status_code == 200, upload.text
        assert upload.json()["revision"] == 2
        new_reference_id = upload.json()["reference_asset"]["id"]
        assert new_reference_id != seed.reference_asset_id

        async with application.state.session_factory() as session:
            old_run = await session.get(WorkflowRun, seed.run_id)
            assert old_run is not None
            assert (old_run.stage, old_run.status) == ("S10", "completed")
        assert old_path.is_file()
        assert old_path.read_bytes() == old_bytes
        preview = await client.get(f"/api/assets/{seed.reference_asset_id}")
        assert preview.status_code == 200
        assert preview.content == old_bytes
        assert stub.role_calls_received == []


@pytest.mark.asyncio
async def test_late_old_run_artifact_cannot_replace_current_version(full_settings):
    async with _env(full_settings) as (application, client, seed, _, _):
        response = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="换浅灰背景",
        )
        assert response.status_code == 200, response.text
        new_run_id = response.json()["run_id"]

        async with application.state.session_factory() as session:
            now = format_timestamp(utc_now())
            payload = _prompt_payload()
            payload["positive_prompt"] = "迟到的旧任务结果"
            session.add(
                Artifact(
                    id=str(uuid4()),
                    run_id=seed.run_id,
                    project_id=seed.project_id,
                    kind="prompt_plan",
                    revision_no=2,
                    status="candidate",
                    payload_json=json.dumps(payload),
                    digest=digest_payload(payload),
                    created_at=now,
                )
            )
            await session.commit()

        view = (await client.get(f"/api/projects/{seed.project_id}/workflow")).json()
        assert view["run_id"] == new_run_id
        assert view["stage"] == "S03"
        assert len(view.get("versions") or []) == 1
        assert view["versions"][0]["run_id"] == seed.run_id
        assert view["versions"][0]["brief"]["product_description"] == "米色托特包"


def _coverage(brief: dict[str, Any], reference_id: str | None) -> list[dict[str, Any]]:
    items = [
        ("hard:subject", "positive_prompt", brief["product_description"]),
        ("hard:purpose", "positive_prompt", brief["purpose"]),
        ("hard:creative", "positive_prompt", brief["creative_direction"]),
        ("hard:style", "positive_prompt", "、".join(brief["style_keywords"])),
        ("hard:composition", "positive_prompt", brief["composition"]),
        ("hard:lighting", "positive_prompt", brief["lighting"]),
    ]
    for index, text in enumerate(brief.get("texts") or []):
        items.append((f"hard:text:{index}", "positive_prompt", text))
    for index, fact in enumerate(brief.get("facts") or []):
        items.append((f"hard:fact:{index}", "positive_prompt", fact["text"]))
    for index, value in enumerate(brief.get("must_include") or []):
        items.append((f"hard:must_include:{index}", "positive_prompt", value))
    for index, value in enumerate(brief.get("must_preserve") or []):
        items.append((f"hard:must_preserve:{index}", "positive_prompt", value))
    for index, value in enumerate(brief.get("forbidden") or []):
        items.append((f"hard:forbidden:{index}", "negative_prompt", value))
    items.append(("hard:size", "size", brief["size"]))
    items.append(("hard:count", "count", "1"))
    if reference_id:
        items.append(("hard:reference_asset_id", "reference_asset_id", reference_id))
    return [{"rule_id": rule, "binding": binding, "value": value} for rule, binding, value in items]


def _evidence_for(rule: dict[str, Any], allowed: list[str]) -> list[str]:
    evidence: list[str] = []
    for evidence_type in rule.get("evidence_types", []):
        match = next((item for item in allowed if item.startswith(f"{evidence_type}:")), None)
        if match is not None:
            evidence.append(match)
    return evidence or allowed[:1]


def _full_chain_responder(seed: Seed, brief: dict[str, Any]):
    visual_plan = {
        "style_keywords": ["简洁"],
        "composition": "正面居中",
        "lighting": "柔光",
        "background": "浅色背景",
        "palette": ["米色"],
        "text_layout": "无画面文字",
        "reference_treatment": "保留参考图的商品外观",
        "preservation_rules": ["米色", "一只托特包"],
    }
    prompt = {
        "positive_prompt": "米色托特包，电商主图，简洁棚拍，简洁，正面居中，柔光，一只托特包，米色，保留参考图的商品外观",
        "negative_prompt": "",
        "size": "8x8",
        "count": 1,
        "reference_asset_id": seed.reference_asset_id,
        "requirement_coverage": _coverage(brief, seed.reference_asset_id),
    }

    def responder(request: Any) -> dict[str, Any]:
        task = request.payload["task"]
        role = task["role"]
        input_data = request.payload.get("input_data") or {}
        if role == "requirements_consultant":
            data = dict(brief)
            data["reference_asset_id"] = seed.reference_asset_id
            data["reference_usage"] = "product" if seed.reference_asset_id else "none"
            return {
                "role": role,
                "status": "ready",
                "data": data,
                "issues": [],
                "questions": [],
            }
        if role == "creative_planner":
            return {
                "role": role,
                "status": "ready",
                "data": {
                    "direction": "简洁背景突出托特包",
                    "selling_points": [],
                    "copy_lines": [],
                },
                "issues": [],
                "questions": [],
            }
        if role == "visual_director":
            return {"role": role, "status": "ready", "data": visual_plan, "issues": [], "questions": []}
        if role == "generation_engineer":
            requirements = input_data.get("hard_requirements") or []
            positive_parts: list[str] = []
            negative_parts: list[str] = []
            coverage: list[dict[str, Any]] = []
            for item in requirements:
                binding = (item.get("allowed_bindings") or ["positive_prompt"])[0]
                value = str(item["requirement"])
                coverage.append(
                    {"rule_id": item["rule_id"], "binding": binding, "value": value}
                )
                if binding == "positive_prompt":
                    positive_parts.append(value)
                elif binding == "negative_prompt":
                    negative_parts.append(value)
            data = {
                "positive_prompt": "；".join(positive_parts),
                "negative_prompt": "；".join(negative_parts),
                "size": next(
                    (
                        item["requirement"]
                        for item in requirements
                        if item["rule_id"] == "hard:size"
                    ),
                    "8x8",
                ),
                "count": 1,
                "reference_asset_id": (
                    next(
                        (
                            item["requirement"]
                            for item in requirements
                            if item["rule_id"] == "hard:reference_asset_id"
                        ),
                        None,
                    )
                ),
                "requirement_coverage": coverage,
            }
            return {"role": role, "status": "ready", "data": data, "issues": [], "questions": []}
        if role == "quality_compliance":
            rules = input_data.get("rules") or []
            allowed = list(task.get("allowed_evidence_ids") or [])
            checks = [
                {
                    "rule_id": rule["rule_id"],
                    "outcome": "pass",
                    "evidence_ids": _evidence_for(rule, allowed),
                    "note": "已核对",
                }
                for rule in rules
                if rule.get("applicability") == "applicable"
            ]
            return {
                "role": role,
                "status": "ready",
                "data": {
                    "asset_id": input_data.get("asset_id"),
                    "decision": "pass",
                    "checks": checks,
                    "soft_feedback": [],
                    "limitations": [],
                    "user_summary": "图片符合确认要求。",
                },
                "issues": [],
                "questions": [],
            }
        if role == "delivery_manager":
            target_asset = input_data.get("target_asset") or {}
            requirement = input_data.get("requirement") or {}
            return {
                "role": role,
                "status": "ready",
                "data": {
                    "asset_id": target_asset.get("asset_id"),
                    "requirement_revision": requirement.get("revision"),
                    "filename_hint": "bag-main.png",
                    "caption": "已按当前需求完成检查。",
                },
                "issues": [],
                "questions": [],
            }
        if role == "output_reviewer":
            targets = request.payload.get("review_targets") or []
            allowed = list(task.get("allowed_evidence_ids") or [])
            results = []
            for target in targets:
                target_id = str(target["target_id"])
                if target.get("target_kind") == "quality_report":
                    checks = [
                        {
                            "rule_id": "review:quality_report",
                            "outcome": "pass",
                            "evidence_ids": [f"report:{target.get('artifact_id')}"],
                            "note": "报告与检查一致",
                        }
                    ]
                else:
                    rule_ids = list(target.get("required_rule_ids") or task.get("required_rule_ids") or [])
                    by_id = {
                        rule["rule_id"]: rule
                        for rule in (target.get("rules") or [])
                        if isinstance(rule, dict) and "rule_id" in rule
                    }
                    checks = [
                        {
                            "rule_id": rule_id,
                            "outcome": "pass",
                            "evidence_ids": _evidence_for(by_id.get(rule_id, {}), allowed)
                            or allowed[:1]
                            or [target_id],
                            "note": "已核对",
                        }
                        for rule_id in rule_ids
                    ]
                results.append(
                    {"target_id": target_id, "decision": "pass", "checks": checks}
                )
            return {
                "role": role,
                "status": "ready",
                "data": {"results": results},
                "issues": [],
                "questions": [],
            }
        raise AssertionError(f"unexpected role {role!r}")

    return responder


@pytest.mark.asyncio
async def test_full_new_version_chain_preserves_old_and_publishes_new(full_settings):
    capabilities = replace(
        VisualStub().capabilities,
        supported_image_sizes=("8x8",),
        provider_max_reference_bytes=5_000_000,
    )
    visual = VisualStub(
        capabilities=capabilities,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=_png_bytes((30, 120, 220), 8), image_count=1
        ),
    )
    settings = full_settings.model_copy(update={"visual_image_size": "8x8"})
    stub = LLMStub()
    async with _env(
        settings, with_reference=True, stub=stub, visual=visual
    ) as (application, client, seed, stub, visual):
        old_brief = _brief_payload()
        old_brief["reference_asset_id"] = seed.reference_asset_id
        old_brief["reference_usage"] = "product"
        respond = _full_chain_responder(seed, old_brief)
        stub.role_responder = respond

        started = await _post(
            client,
            seed.project_id,
            "request_new_version",
            revision=1,
            text="更换更简洁的背景",
        )
        assert started.status_code == 200, started.text
        view = started.json()
        assert view["stage"] == "S03"
        new_run_id = view["run_id"]
        assert new_run_id != seed.run_id
        assert visual.submit_calls == []

        async def act(action: str, *, text: str | None = None, payload: dict[str, Any] | None = None):
            nonlocal view
            response = await _post(
                client,
                seed.project_id,
                action,
                revision=view["project_revision"],
                text=text,
                payload=payload,
            )
            assert response.status_code == 200, (action, response.text)
            view = response.json()
            return view

        view = await act("compose_brief")
        assert view["stage"] == "S04"
        view = await act("plan_creative")
        assert view["stage"] == "S05"
        view = await act("plan_visual")
        view = await act("finalize_requirements")
        assert view["stage"] == "S06"
        view = await act("confirm_requirements")
        assert view["stage"] == "S07"
        view = await act("compile_prompt")
        assert view["stage"] == "S08"

        async with application.state.session_factory() as session:
            jobs_before = (await session.scalars(select(GenerationJob))).all()
        assert len(jobs_before) == 1
        assert visual.submit_calls == []

        view = await act("submit_generation")
        for _ in range(200):
            tasks = list(application.state.generation_tasks)
            if not tasks:
                break
            await asyncio.gather(*tasks, return_exceptions=True)
        fetched = (await client.get(f"/api/projects/{seed.project_id}/workflow")).json()
        assert fetched["stage"] == "S09"
        view = fetched
        view = await act("review_candidate")
        assert view["stage"] == "S10"
        view = await act("prepare_delivery")
        view = await act("approve_delivery")
        view = await act("publish_delivery")
        assert view["stage"] == "S10"
        assert view["status"] == "completed"

        versions = view.get("versions") or []
        assert len(versions) == 2
        assert versions[0]["run_id"] == seed.run_id
        assert versions[1]["run_id"] == new_run_id
        assert versions[1]["can_download"] is True
        assert versions[0]["can_download"] is True
        new_asset_id = versions[1]["asset_id"]
        assert new_asset_id and new_asset_id != seed.asset_id
        old_preview = await client.get(f"/api/assets/{seed.asset_id}")
        old_download = await client.get(f"/api/assets/{seed.asset_id}?download=true")
        new_preview = await client.get(f"/api/assets/{new_asset_id}")
        new_download = await client.get(f"/api/assets/{new_asset_id}?download=true")
        assert old_preview.status_code == 200 and old_preview.content == seed.asset_bytes
        assert old_download.status_code == 200 and old_download.content == seed.asset_bytes
        assert new_preview.status_code == 200
        assert new_download.status_code == 200
        assert new_download.content != seed.asset_bytes
        assert len(visual.submit_calls) == 1

        async with application.state.session_factory() as session:
            new_job = await session.scalar(
                select(GenerationJob).where(GenerationJob.run_id == new_run_id)
            )
            old_run = await session.get(WorkflowRun, seed.run_id)
            new_run = await session.get(WorkflowRun, new_run_id)
        assert new_job is not None and new_job.status == "published"
        assert old_run is not None and old_run.status == "completed"
        assert new_run is not None and new_run.status == "completed"
