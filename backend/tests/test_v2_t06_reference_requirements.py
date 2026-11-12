from __future__ import annotations

import hashlib
import io
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.db import write_transaction
from app.main import create_app
from app.models import Asset, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import state
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from app.workflow.tables import Artifact, GenerationJob
from tests.conftest import migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client

INTENT_RULES = ("G01_SCOPE", "G01_ACTION")
G02_RULES = (
    "G02_IMAGE_VISIBLE",
    "G02_OBSERVATION_GROUNDED",
    "G02_TEXT_IS_DATA",
    "G02_NO_UNSUPPORTED_INFERENCE",
)


def png_bytes(color: tuple[int, int, int] = (230, 230, 230)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(output, format="PNG")
    return output.getvalue()


def _intent(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "intent_router",
        "status": "ready",
        "data": {"route": "image_request", "action": "new_request", "evidence_ids": [evidence]},
        "issues": [],
        "questions": [],
    }


def _review_checks(
    target_id: str, mode: str = "pass", rules: tuple[str, ...] = REVIEW_RULE_IDS
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, rule_id in enumerate(rules):
        outcome = "pass"
        if mode == "unknown" and index == 0:
            outcome = "unknown"
        if mode == "revise" and index == 0:
            outcome = "fail"
        checks.append(
            {
                "rule_id": rule_id,
                "outcome": outcome,
                "evidence_ids": [target_id],
                "note": "",
            }
        )
    return checks


def _review_response(
    request: Any, mode: str = "pass", rules: tuple[str, ...] = REVIEW_RULE_IDS
) -> dict[str, Any]:
    target = request.payload["review_targets"][0]
    target_id = target["target_id"]
    decision = mode if mode in {"revise", "reject", "unknown"} else "pass"
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": decision,
                    "checks": _review_checks(target_id, mode, rules),
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


def _ready(
    request: Any,
    *,
    usage: str,
    asset_id: str | None,
    facts: list[dict[str, Any]] | None = None,
    product_description: str = "用户提供的陶瓷杯",
    reference_usage_field: str | None = None,
) -> dict[str, Any]:
    message_evidence = request.payload["task"]["allowed_evidence_ids"][0]
    data: dict[str, Any] = {
        "product_description": product_description,
        "purpose": "电商商品主图",
        "facts": facts
        if facts is not None
        else [
            {
                "text": "用户声明商品为陶瓷杯",
                "basis": "user_declared",
                "evidence_ids": [message_evidence],
            }
        ],
        "creative_direction": None,
        "style_keywords": [],
        "composition": None,
        "lighting": None,
        "texts": [],
        "must_include": [],
        "must_preserve": [],
        "forbidden": [],
        "size": request.payload["capabilities"]["size"] or "1024x1024",
        "reference_asset_id": asset_id,
        "reference_usage": reference_usage_field or usage,
        "assumptions": [],
        "unresolved": [],
    }
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": data,
        "issues": [],
        "questions": [],
    }


def _needs_input(question: str, *, code: str = "CONFLICTING_INPUT") -> dict[str, Any]:
    return {
        "role": "requirements_consultant",
        "status": "needs_input",
        "data": None,
        "issues": [
            {
                "code": code,
                "path": "facts",
                "detail": "用户描述与参考图观察存在冲突",
                "evidence_ids": [],
            }
        ],
        "questions": [question],
    }


def _reference_response(request: Any, observations: list[dict[str, Any]]) -> dict[str, Any]:
    usage = request.payload["input_data"]["reference_usage"]
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    resolved = [{**item, "evidence_ids": [evidence]} for item in observations]
    return {
        "role": "reference_analyst",
        "status": "ready",
        "data": {
            "reference_usage": usage,
            "observations": resolved,
            "texts": [],
            "uncertainties": [],
        },
        "issues": [],
        "questions": [],
    }


def _reference_review_response(request: Any, mode: str = "pass") -> dict[str, Any]:
    target = request.payload["review_targets"][0]
    target_id = target["target_id"]
    image_evidence = "reference:" + target["reference_asset_id"]
    decision = mode if mode in {"revise", "reject", "unknown"} else "pass"
    checks = []
    for rule_id in G02_RULES:
        outcome = "pass"
        if mode == "unknown" and rule_id == "G02_IMAGE_VISIBLE":
            outcome = "unknown"
        if mode in {"revise", "reject"} and rule_id == "G02_OBSERVATION_GROUNDED":
            outcome = "fail"
        checks.append(
            {
                "rule_id": rule_id,
                "outcome": outcome,
                "evidence_ids": [target_id, image_evidence],
                "note": "",
            }
        )
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {"results": [{"target_id": target_id, "decision": decision, "checks": checks}]},
        "issues": [],
        "questions": [],
    }


class Script:
    def __init__(
        self,
        briefs: list[Any],
        reviews: list[str] | None = None,
        *,
        report_observations: list[list[dict[str, Any]]] | None = None,
        reference_reviews: list[str] | None = None,
    ) -> None:
        self.briefs = list(briefs)
        self.reviews = list(reviews or ["pass"])
        self.report_observations = list(report_observations or [])
        self.reference_reviews = list(reference_reviews or ["pass"])
        self.requirements_requests: list[Any] = []
        self.review_requests: list[Any] = []
        self.reference_requests: list[Any] = []
        self.reference_review_requests: list[Any] = []

    def responder(self, request: Any) -> dict[str, Any]:
        task = request.payload["task"]
        role = task["role"]
        if role == "intent_router":
            return _intent(request)
        if role == "reference_analyst":
            index = len(self.reference_requests)
            self.reference_requests.append(request)
            observations = (
                self.report_observations[min(index, len(self.report_observations) - 1)]
                if self.report_observations
                else [
                    {
                        "kind": "color",
                        "description": "参考图中的商品杯身为蓝色",
                        "confidence": "clear",
                    }
                ]
            )
            return _reference_response(request, observations)
        if role == "output_reviewer":
            target = request.payload["review_targets"][0]
            if target.get("target_kind") == "intent_route":
                return _review_response(request, "pass", INTENT_RULES)
            if target.get("target_kind") == "reference_report":
                index = len(self.reference_review_requests)
                self.reference_review_requests.append(request)
                mode = self.reference_reviews[min(index, len(self.reference_reviews) - 1)]
                return _reference_review_response(request, mode)
            self.review_requests.append(request)
            mode = self.reviews[min(len(self.review_requests) - 1, len(self.reviews) - 1)]
            if mode == "provider_error":
                raise ProviderError(
                    "review down", evidence="uncertain", error_code="LLM_UNAVAILABLE"
                )
            return _review_response(request, mode)
        if role == "requirements_consultant":
            item = self.briefs[min(len(self.requirements_requests), len(self.briefs) - 1)]
            self.requirements_requests.append(request)
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                return item(request)
            return item
        raise AssertionError(f"unexpected role {role!r}")


@asynccontextmanager
async def _environment(settings: Any, script: Script):
    stub = LLMStub(role_responder=script.responder)
    visual = VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, stub, visual


async def _create_project(client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def _upload(
    client: AsyncClient, project_id: str, revision: int, color=(20, 80, 200)
) -> dict[str, Any]:
    response = await client.post(
        f"/api/projects/{project_id}/reference",
        files={"file": ("reference.png", png_bytes(color), "image/png")},
        data={"expected_revision": str(revision)},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _seed_replacement_asset(
    application: Any, project_id: str, color: tuple[int, int, int] = (200, 30, 30)
) -> str:
    """Create a real temporary reference file and point the project at it.

    The v1 upload endpoint is intentionally closed once a v2 workflow exists;
    #34 owns the v2 replacement action. This helper only seeds the equivalent
    persisted state (file + Asset row + project reference) so T06 can exercise
    the S03 side before #34 is merged.
    """
    data = png_bytes(color)
    asset_id = str(__import__("uuid").uuid4())
    settings = application.state.settings
    relative = f"references/{asset_id}.png"
    path = settings.resolved_data_dir() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    now = format_timestamp(utc_now())
    async with write_transaction(application.state.session_factory) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.revision += 1
        project.reference_asset_id = asset_id
        project.updated_at = now
        session.add(
            Asset(
                id=asset_id,
                project_id=project_id,
                kind="reference",
                relative_path=relative,
                mime_type="image/png",
                width=64,
                height=64,
                byte_size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                created_at=now,
            )
        )
        await session.flush()
    return asset_id


async def _act(
    client: AsyncClient,
    project_id: str,
    action: str,
    *,
    expected_revision: int,
    text: str | None = None,
) -> Any:
    body: dict[str, Any] = {"expected_revision": expected_revision}
    if text is not None:
        body["text"] = text
    return await client.post(f"/api/projects/{project_id}/workflow/actions/{action}", json=body)


async def _view(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _seed_report(
    application: Any,
    project_id: str,
    *,
    asset_id: str,
    usage: str,
    observations: list[dict[str, Any]] | None = None,
    texts: list[dict[str, Any]] | None = None,
    uncertainties: list[str] | None = None,
    approve: bool = True,
):
    ctx = await state.load_context(application, project_id)
    evidence = f"reference:{asset_id}"
    payload = {
        "reference_usage": usage,
        "observations": observations
        if observations is not None
        else [
            {
                "kind": "color",
                "description": "参考图中的商品杯身为蓝色",
                "confidence": "clear",
                "evidence_ids": [evidence],
            }
        ],
        "texts": texts or [],
        "uncertainties": uncertainties or [],
    }
    artifact = await state.start_artifact(ctx, "reference_report", payload)
    if approve:
        await state.add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision="pass",
            checks={
                "checks": [
                    {
                        "rule_id": "G02_IMAGE_VISIBLE",
                        "outcome": "pass",
                        "evidence_ids": [evidence],
                        "note": "ok",
                    }
                ]
            },
        )
    return artifact


async def _prepare(
    application: Any,
    client: AsyncClient,
    *,
    usage: str = "product",
    observations: list[dict[str, Any]] | None = None,
    approve: bool = True,
    text: str = "请用这张参考图做一个电商商品主图",
) -> dict[str, Any]:
    project = await _create_project(client)
    uploaded = await _upload(client, project["id"], project["revision"])
    asset_id = uploaded["reference_asset"]["id"]
    started = await _act(
        client,
        project["id"],
        "submit_request",
        text=text,
        expected_revision=uploaded["revision"],
    )
    assert started.status_code == 200, started.text
    if observations is not None or usage != "product" or not approve:
        await _seed_report(
            application,
            project["id"],
            asset_id=asset_id,
            usage=usage,
            observations=observations,
            approve=approve,
        )
    ctx = await state.load_context(application, project["id"])
    if ctx.run is None or ctx.run.stage != "S03":
        await state.set_stage(ctx, "S03", "active")
    view = await _view(client, project["id"])
    return {
        "project_id": project["id"],
        "asset_id": asset_id,
        "run_id": ctx.run_id,
        "revision": view["project_revision"],
    }


async def _artifacts(application: Any, run_id: str, kind: str) -> list[Artifact]:
    async with application.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Artifact)
                    .where(Artifact.run_id == run_id, Artifact.kind == kind)
                    .order_by(Artifact.revision_no.asc())
                )
            ).all()
        )


async def _generation_jobs(application: Any, project_id: str) -> list[GenerationJob]:
    async with application.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(GenerationJob).where(GenerationJob.project_id == project_id)
                )
            ).all()
        )


async def test_unapproved_reference_report_blocks_requirements_without_model_call(full_settings):
    """AC-14/AC-28：未批准报告不能进入需求整合，也不静默降级为纯文字。"""
    script = Script(
        [lambda request: _ready(request, usage="product", asset_id="unused")],
        reference_reviews=["unknown"],
    )
    async with _environment(full_settings, script) as (application, client, _stub, _visual):
        project = await _create_project(client)
        uploaded = await _upload(client, project["id"], project["revision"])
        asset_id = uploaded["reference_asset"]["id"]
        started = await _act(
            client,
            project["id"],
            "submit_request",
            text="请用这张参考图做商品主图",
            expected_revision=uploaded["revision"],
        )
        assert started.status_code == 200
        ctx = await state.load_context(application, project["id"])
        await state.set_stage(ctx, "S03", "active")
        view = await _view(client, project["id"])

        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REFERENCE_REPORT_NOT_APPROVED"
        assert script.requirements_requests == []
        assert await _artifacts(application, ctx.run_id, "brief_draft") == []
        current = await _view(client, project["id"])
        assert current["stage"] == "S03"
        assert current["status"] == "blocked"
        assert asset_id  # keep the real uploaded asset in the setup


async def test_product_reference_integrates_image_observations_with_actual_evidence(full_settings):
    """AC-11/AC-14/AC-28：商品参考的可见观察以 image_observed 事实和原图证据进入草稿。"""
    project_holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        asset_id = project_holder["asset_id"]
        evidence = f"reference:{asset_id}"
        return _ready(
            request,
            usage="product",
            asset_id=asset_id,
            facts=[
                {
                    "text": "参考图中的商品杯身为蓝色",
                    "basis": "image_observed",
                    "evidence_ids": [evidence],
                },
                {
                    "text": "用户声明商品为陶瓷杯",
                    "basis": "user_declared",
                    "evidence_ids": [request.payload["task"]["allowed_evidence_ids"][0]],
                },
            ],
            product_description="蓝色陶瓷杯",
        )

    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        uploaded = await _upload(client, project["id"], project["revision"])
        asset_id = uploaded["reference_asset"]["id"]
        project_holder["asset_id"] = asset_id
        started = await _act(
            client,
            project["id"],
            "submit_request",
            text="请用这张参考图做商品主图",
            expected_revision=uploaded["revision"],
        )
        assert started.status_code == 200
        report = await _seed_report(application, project["id"], asset_id=asset_id, usage="product")
        ctx = await state.load_context(application, project["id"])
        await state.set_stage(ctx, "S03", "active")
        view = await _view(client, project["id"])

        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stage"] == "S04"
        brief_artifacts = await _artifacts(application, ctx.run_id, "brief_draft")
        assert len(brief_artifacts) == 1 and brief_artifacts[0].status == "approved"
        payload = brief_artifacts[0].payload_json
        assert f"reference:{asset_id}" in payload
        assert "image_observed" in payload
        assert report.id in [
            item.id for item in await _artifacts(application, ctx.run_id, "reference_report")
        ]

        assert len(script.requirements_requests) == 1
        request = script.requirements_requests[0]
        assert request.payload["task"]["role"] == "requirements_consultant"
        assert request.images == ()
        assert request.payload["input_data"]["reference_usage"] == "product"
        assert request.payload["input_data"]["reference_asset_id"] == asset_id
        approved_inputs = request.payload["approved_inputs"]
        assert approved_inputs[0]["kind"] == "reference_report"
        assert (
            approved_inputs[0]["payload"]["observations"][0]["description"]
            == "参考图中的商品杯身为蓝色"
        )
        assert visual.submit_calls == []
        assert await _generation_jobs(application, project["id"]) == []
        assert body["generation_job"] is None


@pytest.mark.parametrize("usage", ["style", "composition"])
async def test_style_and_composition_reference_usage_is_passed_and_preserved(full_settings, usage):
    """AC-28：style/composition 只按对应用途进入需求，不伪装成商品参考。"""
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(request, usage=usage, asset_id=holder["asset_id"])

    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare(application, client, usage=usage)
        holder["asset_id"] = prepared["asset_id"]
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 200, response.text
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        payload = artifacts[0].payload_json
        assert f'"reference_usage": "{usage}"' in payload
        assert f'"reference_asset_id": "{prepared["asset_id"]}"' in payload
        assert script.requirements_requests[0].payload["input_data"]["reference_usage"] == usage
        assert visual.submit_calls == []


async def test_color_and_quantity_conflict_returns_centralized_reviewed_question(full_settings):
    """AC-11：颜色/数量冲突集中澄清，不把任一来源擅自写成确定事实。"""
    observations = [
        {
            "kind": "color",
            "description": "参考图中的商品杯身为蓝色",
            "confidence": "clear",
            "evidence_ids": ["reference:PLACEHOLDER"],
        },
        {
            "kind": "quantity",
            "description": "参考图中有两个杯子",
            "confidence": "clear",
            "evidence_ids": ["reference:PLACEHOLDER"],
        },
    ]
    question = "参考图显示杯身为蓝色且有两个杯子，而你描述为红色一个；请确认颜色和数量以哪个为准？"
    script = Script([_needs_input(question)])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare_with_observations(
            application, client, usage="product", observations=observations
        )
        # The placeholder evidence ids are replaced by the real asset id in seeding.
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stage"] == "S03"
        assert body["status"] == "needs_input"
        assert body["issue"]["message"] == question
        assert await _artifacts(application, prepared["run_id"], "brief_draft") == []
        request = script.requirements_requests[0]
        report_payload = request.payload["approved_inputs"][0]["payload"]
        assert [item["description"] for item in report_payload["observations"]] == [
            "参考图中的商品杯身为蓝色",
            "参考图中有两个杯子",
        ]
        hints = request.payload["input_data"]["reference_conflict_hints"]
        assert [item["kind"] for item in hints] == ["color", "quantity"]
        assert visual.submit_calls == []
        assert await _generation_jobs(application, prepared["project_id"]) == []


async def test_image_observation_may_not_infer_brand_authorization(full_settings):
    """AC-28：可见 Logo/质感只能作为视觉观察，不能推断品牌授权、材质或功效。"""
    observations = [
        {
            "kind": "logo",
            "description": "参考图杯身印有字母 LOGO",
            "confidence": "clear",
            "evidence_ids": ["reference:PLACEHOLDER"],
        }
    ]
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(
            request,
            usage="product",
            asset_id=holder["asset_id"],
            facts=[
                {
                    "text": "该品牌已获官方授权",
                    "basis": "image_observed",
                    "evidence_ids": [f"reference:{holder['asset_id']}"],
                }
            ],
        )

    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare_with_observations(
            application, client, usage="product", observations=observations
        )
        holder["asset_id"] = prepared["asset_id"]
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert await _artifacts(application, prepared["run_id"], "brief_draft") == []
        assert visual.submit_calls == []


async def test_forged_image_evidence_is_rejected(full_settings):
    """AC-15：image_observed 事实必须引用当前原图的后端证据 ID。"""
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(
            request,
            usage="product",
            asset_id=holder["asset_id"],
            facts=[
                {
                    "text": "参考图中的商品杯身为蓝色",
                    "basis": "image_observed",
                    "evidence_ids": ["reference:fake-asset"],
                }
            ],
        )

    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, _stub, _visual):
        prepared = await _prepare(application, client, usage="product")
        holder["asset_id"] = prepared["asset_id"]
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert await _artifacts(application, prepared["run_id"], "brief_draft") == []


async def test_late_old_report_does_not_cover_new_reference(full_settings):
    """AC-24/AC-28：换图后旧报告不能继续使用，迟到候选不覆盖新图像版本。"""
    holder: dict[str, Any] = {}
    script = Script([lambda request: _ready(request, usage="product", asset_id=holder["asset_id"])])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare(application, client, usage="product")
        # Replace with a second real image; the old approved report cites the old asset.
        new_asset_id = await _seed_replacement_asset(application, prepared["project_id"])
        holder["asset_id"] = new_asset_id
        view = await _view(client, prepared["project_id"])
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REFERENCE_REPORT_NOT_APPROVED"
        assert script.requirements_requests == []

        # A new approved report for the new image is required before retrying.
        await _seed_report(
            application, prepared["project_id"], asset_id=new_asset_id, usage="product"
        )
        ctx = await state.load_context(application, prepared["project_id"])
        await state.set_stage(ctx, "S03", "active")
        view = await _view(client, prepared["project_id"])
        retry = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=view["project_revision"],
        )
        assert retry.status_code == 200, retry.text
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert len(artifacts) == 1
        assert f'"reference_asset_id": "{new_asset_id}"' in artifacts[0].payload_json
        request = script.requirements_requests[0]
        assert request.payload["input_data"]["reference_asset_id"] == new_asset_id
        assert visual.submit_calls == []


async def test_late_old_report_candidate_does_not_disable_current_approval(full_settings):
    """AC-24：来自旧图的迟到候选不能覆盖或禁用当前图已批准的报告。"""
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(request, usage="product", asset_id=holder["asset_id"])

    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare(application, client, usage="product")
        holder["asset_id"] = prepared["asset_id"]
        # This late candidate cites a different (old) image and must be ignored
        # rather than becoming the report S03 consumes.
        await _seed_report(
            application,
            prepared["project_id"],
            asset_id="old-asset-id",
            usage="product",
            approve=False,
        )
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 200, response.text
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        request = script.requirements_requests[0]
        assert request.payload["input_data"]["reference_asset_id"] == prepared["asset_id"]
        assert visual.submit_calls == []


async def test_explicit_removal_creates_text_only_version(full_settings):
    """AC-10/AC-28：只有用户显式移除参考图才建立 reference_usage=none 的新版本。"""
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(request, usage="none", asset_id=None)

    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare(application, client, usage="product")
        holder["asset_id"] = prepared["asset_id"]
        detail = (await client.get(f"/api/projects/{prepared['project_id']}")).json()
        removed = await client.delete(
            f"/api/projects/{prepared['project_id']}/reference",
            params={"expected_revision": detail["project"]["revision"]},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["reference_asset"] is None
        restarted = await _act(
            client,
            prepared["project_id"],
            "submit_request",
            text="改用纯文字需求，做一个米色托特包主图",
            expected_revision=removed.json()["revision"],
        )
        assert restarted.status_code == 200, restarted.text
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=restarted.json()["project_revision"],
        )
        assert response.status_code == 200, response.text
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        payload = artifacts[0].payload_json
        assert '"reference_usage": "none"' in payload
        assert '"reference_asset_id": null' in payload
        assert script.requirements_requests[0].payload["approved_inputs"] == []
        assert script.requirements_requests[0].payload["input_data"]["reference_usage"] == "none"
        assert visual.submit_calls == []


async def test_reference_usage_switch_supersedes_old_draft_and_revokes_approval(full_settings):
    """AC-28/AC-33：用途切换形成新版本，旧草稿与确认依赖失效。"""
    holder: dict[str, Any] = {}

    def product_brief(request: Any) -> dict[str, Any]:
        return _ready(request, usage="product", asset_id=holder["asset_id"])

    def style_brief(request: Any) -> dict[str, Any]:
        return _ready(request, usage="style", asset_id=holder["asset_id"])

    script = Script([product_brief, style_brief])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare(application, client, usage="product")
        holder["asset_id"] = prepared["asset_id"]
        first = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert first.status_code == 200, first.text
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        ctx = await state.load_context(application, prepared["project_id"])
        await state.add_approval(
            ctx,
            approval_type="requirements_card",
            artifact=artifacts[0],
            approved_by="user",
            checks={},
        )
        assert await state.latest_approval(ctx, "requirements_card") is not None

        # A new report with a different usage for the same image replaces the old one.
        await _seed_report(
            application, prepared["project_id"], asset_id=prepared["asset_id"], usage="style"
        )
        ctx = await state.load_context(application, prepared["project_id"])
        await state.set_stage(ctx, "S03", "active")
        view = await _view(client, prepared["project_id"])
        second = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=view["project_revision"],
        )
        assert second.status_code == 200, second.text
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert [item.status for item in artifacts] == ["superseded", "approved"]
        assert '"reference_usage": "style"' in artifacts[-1].payload_json
        assert await state.latest_approval(ctx, "requirements_card") is None
        assert script.requirements_requests[1].payload["input_data"]["reference_usage"] == "style"
        assert visual.submit_calls == []


async def test_review_unknown_keeps_reference_candidate_hidden(full_settings):
    """AC-17：参考整合候选审查 unknown 时不展示、不进入创意。"""
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(
            request,
            usage="product",
            asset_id=holder["asset_id"],
            product_description="不得展示的候选商品",
        )

    script = Script([brief], reviews=["unknown"])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        prepared = await _prepare(application, client, usage="product")
        holder["asset_id"] = prepared["asset_id"]
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stage"] == "S03"
        assert body["status"] == "blocked"
        artifacts = await _artifacts(application, prepared["run_id"], "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "rejected"
        public = await _view(client, prepared["project_id"])
        assert all(
            item["payload"] is None or item["status"] == "approved" for item in public["artifacts"]
        )
        assert "不得展示的候选商品" not in str(public)
        assert visual.submit_calls == []


async def test_ready_draft_cannot_silently_choose_one_side_of_a_conflict(full_settings):
    """AC-11：模型忽略冲突直接产出 ready 草稿时，后端在落库前拒绝。"""
    holder: dict[str, Any] = {}

    def brief(request: Any) -> dict[str, Any]:
        return _ready(
            request,
            usage="product",
            asset_id=holder["asset_id"],
            facts=[
                {
                    "text": "用户声明商品为红色杯子",
                    "basis": "user_declared",
                    "evidence_ids": [request.payload["task"]["allowed_evidence_ids"][0]],
                }
            ],
            product_description="红色陶瓷杯",
        )

    observations = [
        {
            "kind": "color",
            "description": "参考图中的商品杯身为蓝色",
            "confidence": "clear",
            "evidence_ids": ["reference:PLACEHOLDER"],
        }
    ]
    script = Script([brief])
    async with _environment(full_settings, script) as (application, client, _stub, _visual):
        prepared = await _prepare_with_observations(
            application, client, usage="product", observations=observations
        )
        holder["asset_id"] = prepared["asset_id"]
        response = await _act(
            client,
            prepared["project_id"],
            "compose_brief",
            expected_revision=prepared["revision"],
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONFLICTING_INPUT"
        assert await _artifacts(application, prepared["run_id"], "brief_draft") == []


async def test_text_only_path_still_works_without_reference(full_settings):
    """AC-13/AC-28：无图分支保持既有行为，不因参考整合破坏纯文字需求。"""
    script = Script([lambda request: _ready(request, usage="none", asset_id=None)])
    async with _environment(full_settings, script) as (application, client, _stub, visual):
        project = await _create_project(client)
        started = await _act(
            client,
            project["id"],
            "submit_request",
            text="做一个米色托特包的商品主图",
            expected_revision=project["revision"],
        )
        assert started.status_code == 200
        assert started.json()["stage"] == "S03"
        view = await _view(client, project["id"])
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S04"
        ctx = await state.load_context(application, project["id"])
        artifacts = await _artifacts(application, ctx.run_id, "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        assert '"reference_usage": "none"' in artifacts[0].payload_json
        assert script.requirements_requests[0].payload["approved_inputs"] == []
        assert visual.submit_calls == []


async def _prepare_with_observations(
    application: Any,
    client: AsyncClient,
    *,
    usage: str,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    project = await _create_project(client)
    uploaded = await _upload(client, project["id"], project["revision"])
    asset_id = uploaded["reference_asset"]["id"]
    started = await _act(
        client,
        project["id"],
        "submit_request",
        text="请用这张参考图做一个商品主图：红色杯子，一个就好",
        expected_revision=uploaded["revision"],
    )
    assert started.status_code == 200, started.text
    resolved = []
    for item in observations:
        resolved.append(
            {
                **item,
                "evidence_ids": [f"reference:{asset_id}"],
            }
        )
    await _seed_report(
        application,
        project["id"],
        asset_id=asset_id,
        usage=usage,
        observations=resolved,
    )
    ctx = await state.load_context(application, project["id"])
    await state.set_stage(ctx, "S03", "active")
    view = await _view(client, project["id"])
    return {
        "project_id": project["id"],
        "asset_id": asset_id,
        "run_id": ctx.run_id,
        "revision": view["project_revision"],
    }
