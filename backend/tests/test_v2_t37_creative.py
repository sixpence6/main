from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.config import Settings
from app.db import write_transaction
from app.main import create_app
from app.workflow import contracts, state
from app.workflow.tables import Artifact, RevisionBudget, RoleCall
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client

BACKEND_DIR = Path(__file__).resolve().parent.parent

REQUIRED_RULES = (
    "G03_SOURCE",
    "G03_COPY",
    "G03_REQUIREMENTS",
    "G03_SINGLE_DIRECTION",
)


def _migrate(settings: Settings) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


@asynccontextmanager
async def _client_for(settings: Settings, registry):
    """真实应用生命周期与临时 SQLite；只替换外部模型适配器。"""
    _migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client


def brief_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "product_description": "用户提供的陶瓷杯",
        "purpose": "电商商品主图",
        "facts": [
            {
                "text": "用户声明商品为陶瓷杯",
                "basis": "user_declared",
                "evidence_ids": ["user:1"],
            }
        ],
        "creative_direction": "以单个杯子为主体展示外观",
        "style_keywords": ["清爽"],
        "composition": "居中呈现一只杯子",
        "lighting": "柔光",
        "texts": [],
        "must_include": ["一只杯子"],
        "must_preserve": ["参考图中的杯形"],
        "forbidden": ["额外文字"],
        "size": "1024x1024",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": [],
        "unresolved": [],
    }
    data.update(overrides)
    return data


def creative_ready(
    direction: str = "简洁背景突出杯子外观",
    *,
    selling_points: list[dict[str, Any]] | None = None,
    copy_lines: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "role": "creative_planner",
        "status": "ready",
        "data": {
            "direction": direction,
            "selling_points": selling_points or [],
            "copy_lines": copy_lines or [],
        },
        "issues": [],
        "questions": [],
    }


def creative_needs_input(code: str = "MISSING_EVIDENCE") -> dict[str, Any]:
    return {
        "role": "creative_planner",
        "status": "needs_input",
        "data": None,
        "issues": [
            {
                "code": code,
                "path": "selling_points",
                "detail": "缺少已批准来源",
                "evidence_ids": ["user:1"],
            }
        ],
        "questions": [],
    }


def review_payload(target_id: str, decision: str) -> dict[str, Any]:
    checks = []
    for rule_id in REQUIRED_RULES:
        outcome = "pass"
        if decision == "revise" and rule_id == "G03_COPY":
            outcome = "fail"
        if decision == "unknown" and rule_id == "G03_REQUIREMENTS":
            outcome = "unknown"
        if decision == "reject" and rule_id == "G03_REQUIREMENTS":
            outcome = "fail"
        evidence = ["user:1"] if rule_id == "G03_SOURCE" else [target_id]
        checks.append(
            {
                "rule_id": rule_id,
                "outcome": outcome,
                "evidence_ids": evidence,
                "note": "内部审查记录",
            }
        )
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {"results": [{"target_id": target_id, "decision": decision, "checks": checks}]},
        "issues": [],
        "questions": [],
    }


class CreativeScript:
    def __init__(
        self,
        creatives: list[Any],
        reviews: list[str] | None = None,
    ) -> None:
        self.creatives = list(creatives)
        self.reviews = list(reviews or [])
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        role = str(request.payload["task"]["role"])
        if role == "creative_planner":
            item = self.creatives.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if role == "output_reviewer":
            decision = self.reviews.pop(0)
            target_id = str(request.payload["review_targets"][0]["target_id"])
            return review_payload(target_id, decision)
        raise AssertionError(f"unexpected role {role}")


async def _prepare_brief(
    application,
    project_id: str,
    *,
    overrides: dict[str, Any] | None = None,
) -> str:
    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx, "S04")
    ctx = await state.reload_context(ctx)
    artifact = await state.start_artifact(ctx, "brief_draft", brief_payload(**(overrides or {})))
    await state.add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={
            "checks": [
                {
                    "rule_id": "G03_BRIEF",
                    "outcome": "pass",
                    "evidence_ids": ["user:1"],
                    "note": "approved",
                }
            ]
        },
    )
    return ctx.run_id


async def _create_project(client: AsyncClient) -> str:
    response = await client.post("/api/projects", json={})
    assert response.status_code in {200, 201}, response.text
    return response.json()["id"]


async def _workflow(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _artifacts(application, run_id: str, kind: str) -> list[Artifact]:
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


async def _role_calls(application, run_id: str) -> list[RoleCall]:
    async with application.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(RoleCall)
                    .where(RoleCall.run_id == run_id)
                    .order_by(RoleCall.started_at.asc())
                )
            ).all()
        )


async def test_plan_creative_uses_one_direction_and_a_separate_review(llm_settings):
    """AC-14：创意只产生一个方向，生产与审查分开调用，批准后才进入 S05。"""
    script = CreativeScript([creative_ready()], ["pass"])
    stub = LLMStub(role_responder=script)
    visual = VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        view = response.json()

        assert view["stage"] == "S05"
        assert view["status"] == "active"
        creative = [item for item in view["artifacts"] if item["kind"] == "creative_direction"]
        assert len(creative) == 1
        assert creative[0]["status"] == "approved"
        assert creative[0]["payload"]["direction"] == "简洁背景突出杯子外观"
        assert creative[0]["payload"]["selling_points"] == []
        assert creative[0]["payload"]["copy_lines"] == []
        assert view["generation_job"] is None
        assert visual.submit_calls == []

        roles = [str(item.payload["task"]["role"]) for item in stub.role_calls_received]
        assert roles == ["creative_planner", "output_reviewer"]
        creative_request, review_request = stub.role_calls_received
        assert creative_request.payload["task"]["stage"] == "S04"
        assert creative_request.payload["task"]["allowed_evidence_ids"] == ["user:1"]
        assert review_request.payload["review_targets"][0]["target_id"] == creative[0]["id"]
        assert review_request.payload["review_targets"][0]["approved_brief"]["size"] == "1024x1024"
        assert "brief_draft" in review_request.payload["approved_inputs"]

        calls = await _role_calls(application, run_id)
        assert [call.role for call in calls] == ["creative_planner", "output_reviewer"]
        assert all(call.status == "ok" and call.attempt == 1 for call in calls)
        assert all(call.capability == "TEXT" for call in calls)


async def test_requested_copy_is_preserved_verbatim(llm_settings):
    """AC-14：已要求的准确文案原样保留，不新增额外文案。"""
    script = CreativeScript([creative_ready(copy_lines=["限时新品"])], ["pass"])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        await _prepare_brief(application, project_id, overrides={"texts": ["限时新品"]})

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        creative = [
            item for item in response.json()["artifacts"] if item["kind"] == "creative_direction"
        ]
        assert creative[0]["status"] == "approved"
        assert creative[0]["payload"]["copy_lines"] == ["限时新品"]


async def test_extra_copy_without_authorization_is_rejected(llm_settings):
    """AC-14：无文字授权时模型不得自创文案。"""
    script = CreativeScript([creative_ready(copy_lines=["春季新品"])], [])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REQUIREMENT_DRIFT"
        assert await _artifacts(application, run_id, "creative_direction") == []
        view = await _workflow(client, project_id)
        assert view["stage"] == "S04"
        assert view["status"] == "blocked"
        assert [call.role for call in await _role_calls(application, run_id)] == [
            "creative_planner"
        ]


async def test_explicit_copy_authorization_allows_new_copy_lines(llm_settings):
    """AC-14：获批需求明确授权创作文案时，创意可提出待最终确认的新文字。"""
    script = CreativeScript([creative_ready(copy_lines=["把春光装进日常"])], ["pass"])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        await _prepare_brief(
            application,
            project_id,
            overrides={"assumptions": ["用户授权创作一句画面文案，需进入最终需求卡确认"]},
        )

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        creative = [
            item for item in response.json()["artifacts"] if item["kind"] == "creative_direction"
        ]
        assert creative[0]["status"] == "approved"
        assert creative[0]["payload"]["copy_lines"] == ["把春光装进日常"]
        assert stub.role_calls_received[0].payload["input_data"]["copy_policy"] == {
            "approved_texts": [],
            "copy_creation_authorized": True,
        }


async def test_fact_drift_is_rejected_before_persisting(llm_settings):
    """AC-14：无来源的功效／认证类卖点不进入候选。"""
    script = CreativeScript(
        [creative_ready(selling_points=[{"text": "国家级认证", "evidence_ids": ["user:1"]}])],
        [],
    )
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REQUIREMENT_DRIFT"
        assert await _artifacts(application, run_id, "creative_direction") == []
        assert [call.role for call in await _role_calls(application, run_id)] == [
            "creative_planner"
        ]


async def test_forged_evidence_id_is_rejected(llm_settings):
    """AC-14/AC-15：伪造证据 ID 不获得批准。"""
    script = CreativeScript(
        [creative_ready(selling_points=[{"text": "陶瓷杯", "evidence_ids": ["user:99"]}])],
        [],
    )
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REQUIREMENT_DRIFT"
        assert await _artifacts(application, run_id, "creative_direction") == []


async def test_review_unknown_keeps_candidate_isolated(llm_settings):
    """AC-14/AC-17：审查 unknown 不放行，候选不出现在公共视图。"""
    script = CreativeScript([creative_ready(direction="私密候选方向")], ["unknown"])
    stub = LLMStub(role_responder=script)
    visual = VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S04"
        assert response.json()["status"] == "blocked"
        assert response.json()["issue"]["code"] == "REVIEW_UNKNOWN"

        candidate = await _artifacts(application, run_id, "creative_direction")
        assert len(candidate) == 1 and candidate[0].status == "candidate"

        view = await _workflow(client, project_id)
        creative = [item for item in view["artifacts"] if item["kind"] == "creative_direction"]
        assert creative[0]["status"] == "candidate"
        assert creative[0]["payload"] is None
        assert "私密候选方向" not in json.dumps(view, ensure_ascii=False)
        assert view["generation_job"] is None
        assert visual.submit_calls == []


async def test_review_revise_then_manual_revision_supersedes_old_candidate(llm_settings):
    """AC-14：修订需用户手动触发；旧候选被标记，不覆盖新版。"""
    script = CreativeScript(
        [
            creative_ready(direction="旧方向"),
            creative_ready(direction="更突出杯形的新方向"),
        ],
        ["revise", "pass"],
    )
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        first = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "blocked"
        assert [item["id"] for item in body["available_actions"]] == ["revise_creative"]
        pending = [item for item in body["artifacts"] if item["kind"] == "creative_direction"]
        assert pending[0]["status"] == "candidate"
        assert pending[0]["payload"] is None

        second = await client.post(
            f"/api/projects/{project_id}/workflow/actions/revise_creative",
            json={"expected_revision": 0, "text": "更突出杯形"},
        )
        assert second.status_code == 200, second.text
        view = second.json()
        assert view["stage"] == "S05"
        assert view["project_revision"] == 1
        artifacts = await _artifacts(application, run_id, "creative_direction")
        assert [item.status for item in artifacts] == ["superseded", "approved"]
        approved = [item for item in view["artifacts"] if item["kind"] == "creative_direction"][-1]
        assert approved["revision_no"] == 2
        assert approved["payload"]["direction"] == "更突出杯形的新方向"
        assert all(
            item["payload"] is None or item["kind"] == "brief_draft" or item["status"] == "approved"
            for item in view["artifacts"]
        )

        assert len(script.requests) == 4
        assert script.requests[2].payload["input_data"]["revision_note"] == "更突出杯形"
        assert script.requests[2].payload["repair_codes"] == ["G03_COPY"]


async def test_revision_budget_is_persisted_and_blocks_before_model_call(llm_settings):
    """AC-16：创意文字预算最多三个候选，刷新/重启不重置。"""
    script = CreativeScript([creative_ready()], ["pass"])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)
        async with write_transaction(application.state.session_factory) as session:
            session.add(
                RevisionBudget(
                    run_id=run_id,
                    artifact_kind="creative_direction",
                    used=3,
                    max_allowed=3,
                )
            )
            await session.flush()

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        assert script.requests == []

    # A fresh application lifecycle against the same temporary SQLite keeps the
    # consumed budget; no refresh or restart may reset it.
    async with _client_for(llm_settings, build_test_registry(llm=stub)) as (_application, client):
        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        assert stub.role_calls_received == []


async def test_provider_failure_is_single_attempt_without_retry(llm_settings):
    """AC-14/R04：技术失败不自动重试，也不产生候选。"""
    script = CreativeScript(
        [ProviderError("供应商超时", evidence="rejected", error_code="LLM_TIMEOUT")],
        [],
    )
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert len(script.requests) == 1
        assert await _artifacts(application, run_id, "creative_direction") == []
        calls = await _role_calls(application, run_id)
        assert len(calls) == 1
        assert calls[0].role == "creative_planner"
        assert calls[0].status == "timeout"
        assert calls[0].attempt == 1
        view = await _workflow(client, project_id)
        assert view["stage"] == "S04"
        assert view["status"] == "failed"


async def test_needs_input_returns_to_requirements_without_free_questions(llm_settings):
    """AC-14：需要新事实时回到需求顾问，创意不自问自答。"""
    script = CreativeScript([creative_needs_input()], [])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "MISSING_EVIDENCE"
        assert view["issue"]["message"] == "当前状态不支持此操作，请先完成提示的步骤。"
        assert await _artifacts(application, run_id, "creative_direction") == []
        assert [call.role for call in await _role_calls(application, run_id)] == [
            "creative_planner"
        ]


async def test_contract_extra_field_is_rejected_as_service_unavailable(llm_settings):
    """AC-47：创意输出契约不接受未知字段。"""
    extra = creative_ready()
    extra["data"]["moodboard"] = "不允许的情绪板"
    script = CreativeScript([extra], [])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 503, response.text
        assert await _artifacts(application, run_id, "creative_direction") == []
        calls = await _role_calls(application, run_id)
        assert calls[0].status == "invalid"

    with pytest.raises(contracts.ContractError):
        contracts.validate_role_output("creative_planner", extra)
    valid = contracts.validate_role_output("creative_planner", creative_ready())
    assert valid.status == "ready"


async def test_review_with_forged_evidence_never_approves(llm_settings):
    """AC-14/AC-17：审查记录引用伪造证据时不签发批准。"""
    creative = creative_ready()

    def responder(request):
        role = str(request.payload["task"]["role"])
        if role == "creative_planner":
            return creative
        target_id = str(request.payload["review_targets"][0]["target_id"])
        payload = review_payload(target_id, "pass")
        payload["data"]["results"][0]["checks"][0]["evidence_ids"] = ["forged:99"]
        return payload

    stub = LLMStub(role_responder=responder)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        artifacts = await _artifacts(application, run_id, "creative_direction")
        assert len(artifacts) == 1 and artifacts[0].status == "candidate"
        view = await _workflow(client, project_id)
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_UNAVAILABLE"
        assert view["artifacts"][-1]["payload"] is None


async def test_plan_requires_approved_current_brief(llm_settings):
    """AC-14：未批准或已被新草稿取代的需求不能进入创意。"""
    script = CreativeScript([creative_ready()], ["pass"])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        run_id = await _prepare_brief(application, project_id)

        # A newer draft revision, even a candidate, supersedes the old approval
        # for downstream consumption.
        ctx = await state.load_context(application, project_id)
        await state.start_artifact(ctx, "brief_draft", brief_payload(purpose="新用途"))
        await state.set_stage(ctx, "S04", "active")

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "ARTIFACT_STALE"
        assert stub.role_calls_received == []
        assert await _artifacts(application, run_id, "creative_direction") == []


async def test_stale_expected_revision_does_not_call_model(llm_settings):
    """AC-24/AC-14：旧 revision 的请求在模型调用前被拒绝。"""
    script = CreativeScript([creative_ready()], ["pass"])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 1},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"
        assert stub.role_calls_received == []


async def test_visual_capability_is_never_used_by_creative_stage(llm_settings):
    """AC-14：本阶段只做文字，不调用 VISION/IMAGE_GEN。"""
    script = CreativeScript([creative_ready()], ["pass"])
    stub = LLMStub(role_responder=script)
    visual = VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)

    async with _client_for(llm_settings, registry) as (application, client):
        project_id = await _create_project(client)
        await _prepare_brief(application, project_id)

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/plan_creative",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        assert visual.submit_calls == []
        assert registry.recorder.visual_builds == 0
