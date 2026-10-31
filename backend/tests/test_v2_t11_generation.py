from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.adapters.base import ProviderError, ProviderOutcome
from app.config import Settings
from app.db import read_session, write_transaction
from app.main import create_app
from app.models import Asset, Generation, Project
from app.services.assets import inspect_image
from app.workflow import state
from app.workflow.tables import Approval, GenerationJob
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate
from tests.support.auth import authenticated_client


def png_bytes(width: int = 1024, height: int = 1024, color=(230, 230, 230)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def base_brief(reference_id: str | None = None) -> dict:
    return {
        "product_description": "磨砂玻璃咖啡杯，容量 350 毫升",
        "purpose": "电商主图",
        "facts": [
            {
                "text": "杯身印有蓝色鲸鱼图案",
                "basis": "user_declared",
                "evidence_ids": ["user-brief"],
            }
        ],
        "creative_direction": "清晨窗边自然光，简洁温暖",
        "style_keywords": ["极简", "自然"],
        "composition": "主体居中，留白充足",
        "lighting": "柔和侧光",
        "texts": ["WARM MORNING"],
        "must_include": ["杯身蓝色鲸鱼图案"],
        "must_preserve": ["杯口金色描边"],
        "forbidden": ["价格标签", "促销文案"],
        "size": "1024x1024",
        "reference_asset_id": reference_id,
        "reference_usage": "product" if reference_id else "none",
        "assumptions": [],
        "unresolved": [],
    }


def prompt_payload(brief: dict, reference_id: str | None) -> dict:
    parts = [
        brief["product_description"],
        brief["purpose"],
        brief["creative_direction"],
        "、".join(brief["style_keywords"]),
        brief["composition"],
        brief["lighting"],
        *brief["texts"],
        *[fact["text"] for fact in brief["facts"]],
        *brief["must_include"],
        *brief["must_preserve"],
    ]
    coverage = [
        {"rule_id": "hard:subject", "binding": "positive_prompt", "value": brief["product_description"]},
        {"rule_id": "hard:purpose", "binding": "positive_prompt", "value": brief["purpose"]},
        {"rule_id": "hard:creative", "binding": "positive_prompt", "value": brief["creative_direction"]},
        {"rule_id": "hard:style", "binding": "positive_prompt", "value": "、".join(brief["style_keywords"])},
        {"rule_id": "hard:composition", "binding": "positive_prompt", "value": brief["composition"]},
        {"rule_id": "hard:lighting", "binding": "positive_prompt", "value": brief["lighting"]},
        {"rule_id": "hard:text:0", "binding": "positive_prompt", "value": brief["texts"][0]},
        {"rule_id": "hard:fact:0", "binding": "positive_prompt", "value": brief["facts"][0]["text"]},
        {"rule_id": "hard:must_include:0", "binding": "positive_prompt", "value": brief["must_include"][0]},
        {"rule_id": "hard:must_preserve:0", "binding": "positive_prompt", "value": brief["must_preserve"][0]},
        {"rule_id": "hard:forbidden:0", "binding": "negative_prompt", "value": brief["forbidden"][0]},
        {"rule_id": "hard:forbidden:1", "binding": "negative_prompt", "value": brief["forbidden"][1]},
        {"rule_id": "hard:size", "binding": "size", "value": brief["size"]},
        {"rule_id": "hard:count", "binding": "count", "value": "1"},
    ]
    if reference_id:
        parts.append("参考图仅用于商品外观，不复制背景")
        coverage.append({"rule_id": "hard:reference_asset_id", "binding": "reference_asset_id", "value": reference_id})
    return {
        "positive_prompt": "；".join(parts),
        "negative_prompt": "；".join(brief["forbidden"]),
        "size": brief["size"],
        "count": 1,
        "reference_asset_id": reference_id,
        "requirement_coverage": coverage,
    }


@asynccontextmanager
async def environment(settings: Settings, *, visual: VisualStub | None = None):
    llm = LLMStub()
    visual = visual or VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, llm, visual, registry


async def setup_ready_project(
    app,
    client: AsyncClient,
    *,
    with_reference: bool = False,
    image_size: str = "1024x1024",
) -> dict:
    created_response = await client.post("/api/projects", json={})
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    project_id = created["id"]
    revision = created["revision"]
    reference_id = None
    reference_data = None
    if with_reference:
        reference_data = png_bytes(640, 480)
        uploaded_response = await client.post(
            f"/api/projects/{project_id}/reference",
            files={"file": ("reference.png", reference_data, "image/png")},
            data={"expected_revision": str(revision)},
        )
        assert uploaded_response.status_code == 200, uploaded_response.text
        uploaded = uploaded_response.json()
        revision = uploaded["revision"]
        reference_id = uploaded["reference_asset"]["id"]

    brief = base_brief(reference_id)
    brief["size"] = image_size
    async with write_transaction(app.state.session_factory) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.product_description = brief["product_description"]
        project.purpose = brief["purpose"]
        project.style = "、".join(brief["style_keywords"])
        project.composition = brief["composition"]
        project.text_content = "、".join(brief["texts"])
        project.image_size = image_size
        project.reference_asset_id = reference_id
        project.brief_status = "confirmed"
        project.confirmed_revision = revision
        await session.flush()

    ctx = await state.load_context(app, project_id)
    await state.create_run(ctx, stage="S08")
    ctx = await state.reload_context(ctx)
    final_artifact = await state.start_artifact(ctx, "final_brief", brief)
    await state.add_review(
        ctx,
        final_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={"checks": [{"rule_id": "G03_CONFIRMATION", "outcome": "pass", "evidence_ids": [final_artifact.id], "note": "人工确认"}]},
    )
    approved_final = await state.latest_artifact(ctx, "final_brief", status="approved")
    assert approved_final is not None
    await state.add_approval(
        ctx,
        approval_type="requirements_card",
        artifact=approved_final,
        approved_by="user",
        checks={"confirmed": True},
    )
    prompt = prompt_payload(brief, reference_id)
    prompt_artifact = await state.start_artifact(ctx, "prompt_plan", prompt)
    await state.add_review(
        ctx,
        prompt_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={"checks": [{"rule_id": "G03_CONFIRMATION", "outcome": "pass", "evidence_ids": [prompt_artifact.id], "note": "独立审查"}]},
    )
    approved_prompt = await state.latest_artifact(ctx, "prompt_plan", status="approved")
    assert approved_prompt is not None
    await state.add_approval(
        ctx,
        approval_type="generation_prompt",
        artifact=approved_prompt,
        approved_by="output_reviewer",
        checks={"checks": []},
        requirement_digest=approved_final.digest,
    )
    return {
        "project_id": project_id,
        "revision": revision,
        "brief": brief,
        "prompt": prompt,
        "reference_id": reference_id,
        "reference_data": reference_data,
        "final_artifact_id": approved_final.id,
        "final_digest": approved_final.digest,
        "prompt_artifact_id": approved_prompt.id,
        "prompt_digest": approved_prompt.digest,
    }


async def workflow_action(client: AsyncClient, setup: dict, action: str):
    return await client.post(
        f"/api/projects/{setup['project_id']}/workflow/actions/{action}",
        json={"expected_revision": setup["revision"]},
    )


async def drain(app) -> None:
    for _ in range(200):
        tasks = list(app.state.generation_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


async def db_job(app, project_id: str) -> GenerationJob | None:
    async with read_session(app.state.session_factory) as session:
        return await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )


@pytest.mark.asyncio
async def test_submit_without_reference_creates_one_private_pending_candidate(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(), image_count=1
        )
    )
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 200, response.text
        await drain(app)
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        job_view = view["generation_job"]
        assert job_view["status"] == "quarantined", job_view
        assert job_view["submit_attempted"] == 1
        assert job_view["result_asset_id"]
        assert len(visual.submit_calls) == 1
        assert visual.max_active_count == 1
        request = visual.submit_calls[0]
        assert request.reference_path is None
        assert request.prompt == setup["prompt"]["positive_prompt"]
        assert request.image_size == "1024x1024"
        assert "WARM MORNING" in request.prompt
        assert "价格标签" in setup["prompt"]["negative_prompt"]
        assert "data:image" not in response.text
        assert "base64" not in response.text.lower()
        assert "visual.invalid" not in response.text
        assert "sk-test" not in response.text
        assert view["delivery"] is None

        async with read_session(app.state.session_factory) as session:
            asset = await session.get(Asset, job_view["result_asset_id"])
        assert asset is not None
        assert asset.review_status == "quarantined"
        assert asset.sha256
        path = full_settings.resolved_data_dir() / asset.relative_path
        data = path.read_bytes()
        assert inspect_image(data).width == 1024
        assert asset.sha256 == __import__("hashlib").sha256(data).hexdigest()

        # 生成身份与确认快照必须持久化，后续恢复/去重只依赖同一请求身份。
        job = await db_job(app, setup["project_id"])
        async with read_session(app.state.session_factory) as session:
            generation = await session.scalar(
                select(Generation).where(Generation.project_id == setup["project_id"])
            )
        assert job is not None and generation is not None
        assert job.request_id == generation.request_id
        assert job.request_digest == generation.request_digest
        assert job.confirmed_snapshot_digest == setup["final_digest"]
        canonical = json.loads(generation.request_canonical)
        assert canonical["confirmed_snapshot_digest"] == setup["final_digest"]
        assert canonical["run_id"]
        assert canonical["confirmed_revision"] == setup["revision"]
        assert canonical["input_digest"]
        assert canonical["stage"] == "S08"
        assert canonical["attempt"] == 1
        assert canonical["prompt_artifact_id"] == setup["prompt_artifact_id"]
        snapshot = json.loads(generation.brief_snapshot_json)
        assert snapshot["product_description"] == setup["brief"]["product_description"]

        denied = await client.get(f"/api/assets/{asset.id}")
        assert denied.status_code == 409, denied.text
        assert "image" not in denied.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_submit_with_reference_sends_current_original_bytes_and_usage(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(), image_count=1
        )
    )
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client, with_reference=True)
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 200, response.text
        await drain(app)
        assert len(visual.submit_calls) == 1
        request = visual.submit_calls[0]
        assert request.reference_path is not None
        assert request.reference_path.read_bytes() == setup["reference_data"]
        assert request.reference_mime_type == "image/png"
        assert "参考图仅用于商品外观" in request.prompt
        assert request.brief.reference_asset_id == setup["reference_id"]
        assert (await db_job(app, setup["project_id"])).status == "quarantined"


@pytest.mark.asyncio
async def test_duplicate_click_and_read_only_refresh_return_original_job(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(), image_count=1
        ),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        first = await workflow_action(client, setup, "submit_generation")
        assert first.status_code == 200, first.text
        first_job = first.json()["generation_job"]

        second = await workflow_action(client, setup, "submit_generation")
        assert second.status_code == 200, second.text
        second_job = second.json()["generation_job"]
        assert second_job["id"] == first_job["id"]
        assert second_job["request_id"] == first_job["request_id"]
        assert len(visual.submit_calls) == 1

        for _ in range(5):
            refreshed = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
            assert refreshed["generation_job"]["id"] == first_job["id"]
        visual.gate.set()
        await drain(app)
        assert len(visual.submit_calls) == 1
        final = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert final["generation_job"]["id"] == first_job["id"]
        assert final["generation_job"]["status"] == "quarantined"


@pytest.mark.asyncio
async def test_missing_vision_blocks_submission_before_provider_build(full_settings):
    settings = full_settings.model_copy(update={"llm_vision_model": None})
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes(), image_count=1)
    )
    async with environment(settings, visual=visual) as (app, client, _llm, _visual, registry):
        setup = await setup_ready_project(app, client)
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "CAPABILITY_NOT_READY"
        assert visual.submit_calls == []
        assert registry.recorder.visual_builds == 0
        assert await db_job(app, setup["project_id"]) is None


@pytest.mark.asyncio
async def test_changed_revision_or_revoked_prompt_blocks_before_provider_boundary(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes(), image_count=1)
    )
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        ctx = await state.load_context(app, setup["project_id"])
        await state.invalidate_requirements(ctx, "用户修改需求")
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONFIRM_REQUIRED"
        assert visual.submit_calls == []
        assert await db_job(app, setup["project_id"]) is None

    visual2 = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes(), image_count=1)
    )
    async with environment(full_settings, visual=visual2) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        async with write_transaction(app.state.session_factory) as session:
            approval = await session.scalar(
                select(Approval)
                .where(Approval.project_id == setup["project_id"], Approval.approval_type == "generation_prompt")
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            assert approval is not None
            approval.revoked_at = "2026-01-01T00:00:00Z"
            approval.revocation_reason = "test"
            await session.flush()
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_NOT_APPROVED"
        assert visual2.submit_calls == []
        assert await db_job(app, setup["project_id"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        ProviderOutcome(state="succeeded", image_bytes=b"not-an-image", image_count=1),
        ProviderOutcome(state="succeeded", image_bytes=png_bytes(), image_count=2),
        ProviderOutcome(state="succeeded", image_bytes=png_bytes(512, 512), image_count=1),
        ProviderOutcome(state="succeeded", result_locator="https://provider.invalid/result.png", image_count=0),
    ],
)
async def test_bad_remote_results_are_never_published(full_settings, outcome: ProviderOutcome):
    visual = VisualStub(submit_outcome=outcome)
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 200, response.text
        await drain(app)
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["delivery"] is None
        assert view["generation_job"]["status"] == "failed"
        assert view["generation_job"]["result_asset_id"] is None
        assert "provider.invalid" not in json.dumps(view, ensure_ascii=False)
        async with read_session(app.state.session_factory) as session:
            assets = (await session.scalars(select(Asset).where(Asset.project_id == setup["project_id"], Asset.kind == "generated"))).all()
        assert assets == []


@pytest.mark.asyncio
async def test_uncertain_provider_failure_is_unknown_and_not_retried(full_settings):
    visual = VisualStub(
        submit_error=ProviderError(
            "上游连接中断，结果未确认",
            evidence="uncertain",
            error_code="VISUAL_TIMEOUT",
            remote_task_id="remote-unknown-1",
        )
    )
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 200, response.text
        await drain(app)
        assert len(visual.submit_calls) == 1
        job = await db_job(app, setup["project_id"])
        assert job.status == "unknown"
        assert job.submit_attempted == 1
        assert job.remote_task_id == "remote-unknown-1"
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["generation_job"]["status"] == "unknown"
        assert "remote-unknown-1" not in json.dumps(view, ensure_ascii=False)


@pytest.mark.asyncio
async def test_late_result_after_requirement_change_is_marked_superseded(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(), image_count=1
        ),
    )
    async with environment(full_settings, visual=visual) as (app, client, _llm, _visual, _registry):
        setup = await setup_ready_project(app, client)
        response = await workflow_action(client, setup, "submit_generation")
        assert response.status_code == 200, response.text
        for _ in range(100):
            if visual.submit_calls:
                break
            await asyncio.sleep(0.01)
        assert len(visual.submit_calls) == 1

        # 供应商处理中需求发生变更；旧结果返回后只能保留为审计记录。
        ctx = await state.load_context(app, setup["project_id"])
        await state.invalidate_requirements(ctx, "用户修改商品描述")
        gate.set()
        await drain(app)

        job = await db_job(app, setup["project_id"])
        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "SUPERSEDED"
        assert job.result_asset_id is None
        async with read_session(app.state.session_factory) as session:
            generation = await session.scalar(
                select(Generation).where(Generation.project_id == setup["project_id"])
            )
            assert generation is not None
            assert generation.status == "failed"
            assert generation.error_code == "SUPERSEDED"
            assert generation.result_asset_id is not None
            old_asset = await session.get(Asset, generation.result_asset_id)
            assert old_asset is not None
            assert old_asset.review_status == "rejected"
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["generation_job"]["status"] == "failed"
        assert len(visual.submit_calls) == 1
