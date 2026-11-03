from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.acceptance import (
    GenerationIntent,
    GenerationSlot,
    GenerationUsage,
    accept_in_session,
)
from app.adapters.base import ProviderOutcome
from app.config import Settings
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.main import create_app
from app.models import Asset, Project
from app.timeutil import format_timestamp
from app.workflow import state
from app.workflow.tables import Approval, Artifact, Delivery, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate
from tests.support.auth import authenticated_client
from tests.test_v2_t40_prompt_review import (
    base_brief,
    candidate_data,
    engineer_response,
    png_bytes,
    review_response,
)


def _quality_response(request, decision="pass"):
    data = request.payload["input_data"]
    allowed = list(request.payload["task"]["allowed_evidence_ids"])
    by_type = {item.split(":", 1)[0]: item for item in allowed}
    checks = []
    for rule in data["rules"]:
        if rule["applicability"] != "applicable":
            continue
        evidence = [by_type[item] for item in rule["evidence_types"]]
        checks.append(
            {
                "rule_id": rule["rule_id"],
                "outcome": "fail" if decision == "fail" else "pass",
                "evidence_ids": evidence,
                "note": "按需求快照和图片证据检查通过"
                if decision == "pass"
                else "图片不符合已确认要求",
            }
        )
    return {
        "role": "quality_compliance",
        "status": "ready",
        "data": {
            "asset_id": data["asset_id"],
            "decision": decision,
            "checks": checks,
            "soft_feedback": [],
            "limitations": [],
            "user_summary": "质量检查通过"
            if decision == "pass"
            else "图片未通过质量检查",
        },
        "issues": [],
        "questions": [],
    }


def _review_response(request):
    targets = request.payload.get("review_targets") or []
    if not targets:
        raise AssertionError("missing review targets")
    if targets[0].get("target_kind") == "delivery_manifest":
        allowed = list(request.payload["task"]["allowed_evidence_ids"])
        evidence = allowed[:3]
        checks = [
            {
                "rule_id": rule_id,
                "outcome": "pass",
                "evidence_ids": evidence,
                "note": "交付清单按已批图片和需求快照复核",
            }
            for rule_id in (
                "G07_MANIFEST_ASSET",
                "G07_MANIFEST_REQUIREMENT",
                "G07_MANIFEST_EVIDENCE",
                "G07_MANIFEST_FILENAME",
                "G07_MANIFEST_CAPTION",
            )
        ]
        return {
            "role": "output_reviewer",
            "status": "ready",
            "data": {
                "results": [
                    {
                        "target_id": targets[0]["target_id"],
                        "decision": "pass",
                        "checks": checks,
                    }
                ]
            },
            "issues": [],
            "questions": [],
        }

    if len(targets) == 2 and any(
        target.get("target_kind") == "quality_report" for target in targets
    ):
        allowed = list(request.payload["task"]["allowed_evidence_ids"])
        by_type = {item.split(":", 1)[0]: item for item in allowed}
        results = []
        for target in targets:
            if target.get("target_kind") == "quality_report":
                checks = [
                    {
                        "rule_id": "review:quality_report",
                        "outcome": "pass",
                        "evidence_ids": [by_type["report"]],
                        "note": "质量报告与图片证据一致",
                    }
                ]
            else:
                checks = []
                for rule in target.get("rules") or []:
                    if rule.get("applicability") != "applicable":
                        continue
                    evidence = [
                        by_type[item] for item in rule["evidence_types"]
                    ]
                    checks.append(
                        {
                            "rule_id": rule["rule_id"],
                            "outcome": "pass",
                            "evidence_ids": evidence,
                            "note": "独立复核通过",
                        }
                    )
            results.append(
                {
                    "target_id": target["target_id"],
                    "decision": "pass",
                    "checks": checks,
                }
            )
        return {
            "role": "output_reviewer",
            "status": "ready",
            "data": {"results": results},
            "issues": [],
            "questions": [],
        }

    return review_response(targets[0]["target_id"])


def _delivery_response(request):
    target = request.payload["input_data"]["target_asset"]
    requirement = request.payload["input_data"]["requirement"]
    return {
        "role": "delivery_manager",
        "status": "ready",
        "data": {
            "asset_id": target["asset_id"],
            "requirement_revision": requirement["revision"],
            "filename_hint": "delivery.png",
            "caption": "电商主图交付",
        },
        "issues": [],
        "questions": [],
    }


def _responder(request):
    role = request.payload["task"]["role"]
    if role == "generation_engineer":
        brief = request.payload["approved_inputs"]["final_brief"]
        return engineer_response(candidate_data(brief))
    if role == "quality_compliance":
        return _quality_response(request)
    if role == "delivery_manager":
        return _delivery_response(request)
    if role == "output_reviewer":
        return _review_response(request)
    raise AssertionError(f"unexpected role {role}")


@asynccontextmanager
async def _environment(settings: Settings, llm: LLMStub, visual: VisualStub, responder=None):
    llm.role_responder = responder or _responder
    registry = build_test_registry(llm=llm, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client


async def _setup_s06(
    app, client: AsyncClient, reference_asset_id: str | None = None
) -> dict:
    created_response = await client.post("/api/projects", json={})
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    project_id = created["id"]
    revision = created["revision"] + 1
    brief = base_brief(reference_asset_id)
    async with write_transaction(app.state.session_factory) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.product_description = brief["product_description"]
        project.purpose = brief["purpose"]
        project.style = "、".join(brief["style_keywords"])
        project.composition = brief["composition"]
        project.text_content = "、".join(brief["texts"])
        project.image_size = brief["size"]
        project.brief_status = "review"
        project.reference_asset_id = reference_asset_id
        project.revision = revision
        await session.flush()

    ctx = await state.load_context(app, project_id)
    await state.create_run(ctx, stage="S06")
    ctx = await state.reload_context(ctx)
    artifact = await state.start_artifact(ctx, "final_brief", brief)
    await state.add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={
            "checks": [
                {
                    "rule_id": "G03_CONFIRMATION",
                    "outcome": "pass",
                    "evidence_ids": [artifact.id],
                    "note": "人工确认",
                }
            ]
        },
    )
    approved = await state.latest_artifact(ctx, "final_brief", status="approved")
    assert approved is not None
    return {
        "project_id": project_id,
        "revision": revision,
        "brief": brief,
        "final_artifact_id": approved.id,
        "final_digest": approved.digest,
    }


async def _add_foreign_reference(app, project_id: str) -> str:
    now = format_timestamp(app.state.clock.now())
    async with write_transaction(app.state.session_factory) as session:
        asset = Asset(
            id=str(uuid.uuid4()),
            project_id=project_id,
            kind="reference",
            relative_path=f"references/{uuid.uuid4()}.png",
            mime_type="image/png",
            width=10,
            height=8,
            byte_size=0,
            sha256=None,
            review_status="quarantined",
            review_note=None,
            created_at=now,
        )
        session.add(asset)
        await session.flush()
        return asset.id

async def _drain(app) -> None:
    for _ in range(200):
        tasks = list(app.state.generation_tasks)
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


async def _rows(app, project_id: str):
    async with read_session(app.state.session_factory) as session:
        intents = list(
            (
                await session.scalars(
                    select(GenerationIntent)
                    .where(GenerationIntent.project_id == project_id)
                    .order_by(GenerationIntent.created_at)
                )
            ).all()
        )
        usages = list(
            (
                await session.scalars(
                    select(GenerationUsage)
                    .where(GenerationUsage.project_id == project_id)
                    .order_by(GenerationUsage.created_at)
                )
            ).all()
        )
        slots = list(
            (
                await session.scalars(
                    select(GenerationSlot)
                    .where(
                        GenerationSlot.intent_id.in_(
                            [intent.id for intent in intents]
                        )
                    )
                    .order_by(GenerationSlot.created_at)
                )
            ).all()
        )
        return intents, usages, slots


@pytest.mark.asyncio
async def test_one_confirmation_creates_intent_usage_slots_and_full_cycle(
    full_settings,
):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    request_id = str(uuid.uuid4())
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        confirm_action = next(
            item for item in view["available_actions"] if item["id"] == "confirm_requirements"
        )
        assert confirm_action["requires_confirmation"] is True
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": request_id,
                },
            },
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S07"
        assert view["status"] == "active"
        assert view["usage"]["used"] == 1
        assert view["usage"]["daily_limit"] == 20
        assert view["intent"]["request_id"] == request_id
        assert view["intent"]["status"] == "accepted"

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert {slot.scope for slot in slots} == {"personal", "instance", "project"}
        assert all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []
        assert all(
            request.payload["task"]["role"] == "generation_engineer"
            for request in llm.role_calls_received
        )

        replay_payload = {
            "expected_revision": setup["revision"],
            "payload": {
                "artifact_id": setup["final_artifact_id"],
                "artifact_digest": setup["final_digest"],
                "requirement_revision": setup["revision"],
                "request_id": request_id,
            },
        }
        replay = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=replay_payload,
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["intent"]["id"] == view["intent"]["id"]
        app.state.settings.generation_daily_limit = 1
        replay_limited = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=replay_payload,
        )
        assert replay_limited.status_code == 200, replay_limited.text
        assert replay_limited.json()["intent"]["id"] == view["intent"]["id"]
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert len(llm.role_calls_received) == 1
        app.state.settings.generation_daily_limit = 20
        llm.gate = None
        gate.set()
        await _drain(app)
        from tests.support.t02_prompt import final_click
        preview = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert preview["prompt_preview"] is not None and visual.submit_calls == []
        final = await final_click(client, {"project_id": setup["project_id"], "workflow": preview})
        assert final.status_code == 200, final.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert len(visual.submit_calls) == 1
        roles = [request.payload["task"]["role"] for request in llm.role_calls_received]
        assert roles == [
            "generation_engineer",
            "output_reviewer",
            "quality_compliance",
            "output_reviewer",
            "delivery_manager",
            "output_reviewer",
        ]

        workflow = await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )
        assert workflow.status_code == 200, workflow.text
        preparing = workflow.json()
        assert preparing["stage"] == "S10"
        assert preparing["status"] == "needs_input"

        approved = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/approve_delivery",
            json={"expected_revision": preparing["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        ready = approved.json()
        assert ready["status"] == "needs_input"

        published = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/publish_delivery",
            json={"expected_revision": ready["project_revision"]},
        )
        assert published.status_code == 200, published.text
        assert published.json()["stage"] == "S10"
        assert published.json()["status"] == "completed"

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert intents[0].status == "delivered"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        assert all(slot.release_reason == "delivery_published" for slot in slots)

        async with read_session(app.state.session_factory) as session:
            delivery = await session.scalar(
                select(Delivery).where(Delivery.project_id == setup["project_id"])
            )
            asset = await session.get(Asset, delivery.asset_id) if delivery else None
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
        assert delivery is not None
        assert delivery.status == "published"
        assert asset is not None and asset.relative_path.startswith("published/")
        assert run is not None and run.stage == "S10" and run.status == "completed"

@pytest.mark.asyncio
async def test_foreign_reference_owner_is_rejected_without_intent(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual) as (app, client):
        other = await client.post("/api/projects", json={})
        assert other.status_code == 201, other.text
        foreign_asset_id = await _add_foreign_reference(app, other.json()["id"])
        setup = await _setup_s06(
            app, client, reference_asset_id=foreign_asset_id
        )
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REQUIREMENTS_CHANGED"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert intents == [] and usages == [] and slots == []
        assert visual.submit_calls == []

@pytest.mark.asyncio
async def test_same_request_with_changed_parameters_conflicts(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    request_id = str(uuid.uuid4())
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": request_id,
                },
            },
        )
        assert response.status_code == 200, response.text
        intent_id = response.json()["intent"]["id"]

        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, setup["project_id"])
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
            artifact = await session.get(Artifact, setup["final_artifact_id"])
            assert project is not None and run is not None and artifact is not None
            with pytest.raises(ApiError) as exc:
                await accept_in_session(
                    session,
                    settings=app.state.settings,
                    project=project,
                    run=run,
                    operator_account_id=project.owner_account_id,
                    artifact=artifact,
                    requirement_revision=setup["revision"],
                    reference_usage=setup["brief"]["reference_usage"],
                    image_size="2048x2048",
                    request_id=request_id,
                )
        assert exc.value.code == "INTENT_REPLAY_CONFLICT"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert [intent.id for intent in intents] == [intent_id]
        assert len(usages) == 1
        assert len(slots) == 3
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_quota_and_slot_rejections_leave_no_intent_or_confirmation(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual) as (app, client):
        first = await _setup_s06(app, client)
        first_response = await client.post(
            f"/api/projects/{first['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": first["revision"],
                "payload": {
                    "artifact_id": first["final_artifact_id"],
                    "artifact_digest": first["final_digest"],
                    "requirement_revision": first["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert first_response.status_code == 200, first_response.text
        for _ in range(500):
            if llm.role_calls_received:
                break
            await asyncio.sleep(0.01)
        calls_after_first = len(llm.role_calls_received)

        second = await _setup_s06(app, client)
        legacy_confirm = await client.post(
            f"/api/projects/{second['project_id']}/confirm",
            json={"revision": second["revision"]},
        )
        assert legacy_confirm.status_code == 409, legacy_confirm.text
        assert legacy_confirm.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"
        legacy_generation = await client.post(
            f"/api/projects/{second['project_id']}/generations",
            json={
                "request_id": str(uuid.uuid4()),
                "confirmed_revision": second["revision"],
            },
        )
        assert legacy_generation.status_code == 409, legacy_generation.text
        assert legacy_generation.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"
        second_payload = {
            "expected_revision": second["revision"],
            "payload": {
                "artifact_id": second["final_artifact_id"],
                "artifact_digest": second["final_digest"],
                "requirement_revision": second["revision"],
                "request_id": str(uuid.uuid4()),
            },
        }

        app.state.settings.generation_daily_limit = 1
        quota = await client.post(
            f"/api/projects/{second['project_id']}/workflow/actions/confirm_requirements",
            json=second_payload,
        )
        assert quota.status_code == 429, quota.text
        assert quota.json()["error"]["code"] == "GENERATION_LIMIT_REACHED"

        app.state.settings.generation_daily_limit = 20
        slot = await client.post(
            f"/api/projects/{second['project_id']}/workflow/actions/confirm_requirements",
            json=second_payload,
        )
        assert slot.status_code == 429, slot.text
        assert slot.json()["error"]["code"] == "GENERATION_SLOT_BUSY"

        intents, usages, slots = await _rows(app, second["project_id"])
        assert intents == []
        assert usages == []
        assert slots == []
        async with read_session(app.state.session_factory) as session:
            approval = await session.scalar(
                select(Approval).where(
                    Approval.project_id == second["project_id"],
                    Approval.approval_type == "requirements_card",
                )
            )
            project = await session.get(Project, second["project_id"])
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == second["project_id"])
            )
        assert approval is None
        assert project is not None and project.brief_status == "review"
        assert run is not None and run.stage == "S06" and run.status == "active"
        assert len(llm.role_calls_received) == calls_after_first
        assert visual.submit_calls == []

        gate.set()
        await _drain(app)

@pytest.mark.asyncio
async def test_quality_rejection_keeps_count_and_releases_slots(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )

    def rejecting_responder(request):
        if request.payload["task"]["role"] == "quality_compliance":
            return _quality_response(request, decision="fail")
        return _responder(request)

    async with _environment(
        full_settings, llm, visual, responder=rejecting_responder
    ) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert response.status_code == 200, response.text
        gate.set()
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status == "failed"
        assert len(usages) == 1
        assert len(slots) == 3
        assert all(slot.released_at is not None for slot in slots)
        assert all(slot.release_reason == "generation_terminal" for slot in slots)
        assert len(visual.submit_calls) == 1
        async with read_session(app.state.session_factory) as session:
            delivery = await session.scalar(
                select(Delivery).where(Delivery.project_id == setup["project_id"])
            )
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
        assert delivery is None
        assert run is not None and run.stage == "S09" and run.status == "blocked"
        assert run.issue_code == "IMAGE_NOT_APPROVED"

@pytest.mark.asyncio
async def test_quality_retry_can_revise_prompt_and_generate_again(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    quality_calls = {"count": 0}

    def responder(request):
        if request.payload["task"]["role"] == "quality_compliance":
            quality_calls["count"] += 1
            return _quality_response(
                request, decision="fail" if quality_calls["count"] == 1 else "pass"
            )
        return _responder(request)

    async with _environment(
        full_settings, llm, visual, responder=responder
    ) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert response.status_code == 200, response.text
        gate.set()
        await _drain(app)

        awaiting = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert awaiting["stage"] == "S07"
        assert awaiting["prompt_preview"] is not None
        from tests.support.t02_prompt import final_click

        first_final = await final_click(
            client, {"project_id": setup["project_id"], "workflow": awaiting}
        )
        assert first_final.status_code == 200, first_final.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S09"
        assert view["issue"]["code"] == "IMAGE_NOT_APPROVED"
        assert {item["id"] for item in view["available_actions"]} == {
            "recheck_candidate",
            "revise_prompt_and_retry",
            "retry_rejected_generation",
        }

        revised = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/revise_prompt_and_retry",
            json={
                "expected_revision": setup["revision"],
                "text": "所有匾额保持空白，不要任何题字；主体呈现为有底座的微缩建筑模型。",
                "payload": {"acknowledge_new_attempt": True},
            },
        )
        assert revised.status_code == 200, revised.text
        awaiting = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert awaiting["stage"] == "S07"
        assert awaiting["status"] == "needs_input"
        assert awaiting["prompt_preview"] is not None
        # The retry is a new preparation intent: it shows a fresh prompt and
        # must not reach the provider until the user confirms that prompt.
        assert len(visual.submit_calls) == 1

        final = await final_click(
            client,
            {"project_id": setup["project_id"], "workflow": awaiting},
            command_id=str(uuid.uuid4()),
        )
        assert final.status_code == 200, final.text
        await _drain(app)

        assert len(visual.submit_calls) == 2
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 2
        assert intents[0].status == "failed"
        assert intents[1].status in {"accepted", "executing"}
        assert len(usages) == 2
        assert len(slots) == 6
        assert all(slot.released_at is not None for slot in slots[:3])
        assert all(slot.released_at is None for slot in slots[3:])

        final_view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert final_view["stage"] == "S10"
        assert final_view["status"] == "needs_input"
        assert final_view["issue"]["code"] == "DELIVERY_MANIFEST_READY"
