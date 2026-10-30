from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.workflow import state
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from tests.support.adapters import LLMStub, VisualStub, build_test_registry

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _migrate(settings: Settings) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    command.upgrade(config, "head")


@asynccontextmanager
async def _client_for(settings: Settings, registry):
    _migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield application, client


def _brief(**overrides):
    payload = {
        "product_description": "米色亚麻托特包",
        "purpose": "电商主图",
        "facts": [
            {
                "text": "用户描述为米色亚麻托特包",
                "basis": "user_declared",
                "evidence_ids": ["user-turn-1"],
            }
        ],
        "creative_direction": "简洁暖色，突出材质",
        "style_keywords": ["简洁", "暖色"],
        "composition": "居中构图，浅米色背景",
        "lighting": "柔和自然光",
        "texts": ["春季新品"],
        "must_include": ["托特包主体完整", "提手可见"],
        "must_preserve": ["亚麻材质纹理"],
        "forbidden": ["水印", "夸张阴影"],
        "size": "1024x1024",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": ["未指定品牌"],
        "unresolved": [],
    }
    payload.update(overrides)
    return payload


async def _seed(application, client, *, approved: bool = True, payload: dict | None = None):
    project = (await client.post("/api/projects", json={})).json()
    project_id = project["id"]
    patched = await client.patch(
        f"/api/projects/{project_id}/brief",
        json={
            "expected_revision": 0,
            "product_description": "米色亚麻托特包",
            "purpose": "电商主图",
            "style": "简洁暖色，突出材质",
            "composition": "居中构图，浅米色背景",
            "text_content": "春季新品",
        },
    )
    assert patched.status_code == 200, patched.text
    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx, "S06")
    ctx = await state.reload_context(ctx)
    artifact = await state.start_artifact(ctx, "final_brief", payload or _brief())
    if approved:
        await state.add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision="pass",
            checks=[{"rule_id": "T09_CARD_COMPLETE", "outcome": "pass", "note": "卡片字段完整"}],
        )
    ctx = await state.reload_context(ctx)
    current = await state.latest_artifact(ctx, "final_brief")
    assert current is not None
    return project_id, ctx, current


def _action_body(ctx, artifact, **overrides):
    payload = {
        "artifact_id": artifact.id,
        "artifact_digest": artifact.digest,
        "requirement_revision": ctx.run.requirement_revision,
    }
    payload.update(overrides)
    return {"expected_revision": ctx.project.revision, "payload": payload}


def _revision_ready(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": {
            "product_description": "黑色帆布托特包",
            "purpose": "电商主图",
            "facts": [
                {
                    "text": "用户要求改为黑色帆布托特包",
                    "basis": "user_declared",
                    "evidence_ids": [evidence],
                }
            ],
            "creative_direction": "简洁暖色，突出材质",
            "style_keywords": ["简洁", "暖色"],
            "composition": "居中构图，浅米色背景",
            "lighting": "柔和自然光",
            "texts": [],
            "must_include": ["托特包主体完整", "提手可见"],
            "must_preserve": ["帆布材质纹理"],
            "forbidden": ["水印", "夸张阴影"],
            "size": request.payload["capabilities"]["size"] or "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": ["未指定品牌"],
            "unresolved": [],
        },
        "issues": [],
        "questions": [],
    }


def _revision_review(target_id: str) -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": "pass",
                    "checks": [
                        {
                            "rule_id": rule_id,
                            "outcome": "pass",
                            "evidence_ids": [target_id],
                            "note": "",
                        }
                        for rule_id in REVIEW_RULE_IDS
                    ],
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


def _revision_responder(request: Any) -> dict[str, Any]:
    role = request.payload["task"]["role"]
    if role == "requirements_consultant":
        return _revision_ready(request)
    target = request.payload["review_targets"][0]
    return _revision_review(target["target_id"])


async def test_confirm_binds_current_approved_card_and_does_not_generate(full_settings):
    """AC-21：确认只记录当前卡片快照，不编译提示词、不调用生图供应商。"""
    llm = LLMStub()
    visual = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    async with _client_for(full_settings, registry) as (application, client):
        project_id, ctx, artifact = await _seed(application, client)

        before = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert before["available_actions"][0]["id"] == "confirm_requirements"

        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(ctx, artifact),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stage"] == "S07"
        assert body["status"] == "active"
        assert body["generation_job"] is None
        assert len(body["approvals"]) == 1
        approval = body["approvals"][0]
        assert approval["approval_type"] == "requirements_card"
        assert approval["approved_by"] == "user"
        assert approval["revoked_at"] is None
        snapshot = approval["checks"]["confirmation"]
        assert snapshot["artifact_id"] == artifact.id
        assert snapshot["artifact_digest"] == artifact.digest
        assert snapshot["artifact_revision_no"] == artifact.revision_no
        assert snapshot["project_revision"] == ctx.project.revision
        assert snapshot["requirement_revision"] == ctx.run.requirement_revision
        assert snapshot["schema_version"] == "2.0.0"
        hard_ids = [item["id"] for item in snapshot["hard_requirements"]]
        assert hard_ids and len(hard_ids) == len(set(hard_ids))
        assert any(item["applies_to"] == "negative_prompt" for item in snapshot["hard_requirements"])
        assert any(item["value"] == "1024x1024" for item in snapshot["hard_requirements"])
        assert llm.calls == []
        assert visual.submit_calls == []

        # 重复确认（包括迟到重放）只返回原确认，不新增审批、生成或供应商调用。
        replay = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(ctx, artifact),
        )
        assert replay.status_code == 200, replay.text
        replay_body = replay.json()
        assert replay_body["stage"] == "S07"
        assert len([a for a in replay_body["approvals"] if a["revoked_at"] is None]) == 1
        assert replay_body["generation_job"] is None
        assert llm.calls == []
        assert visual.submit_calls == []


async def test_confirm_rejects_stale_revision_and_forged_digest(full_settings):
    """AC-21：客户端自报确认/伪造摘要不能通过，也不产生审批或生图。"""
    visual = VisualStub()
    registry = build_test_registry(visual=visual)
    async with _client_for(full_settings, registry) as (application, client):
        project_id, ctx, artifact = await _seed(application, client)

        stale = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json={**_action_body(ctx, artifact), "expected_revision": ctx.project.revision - 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"

        forged = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(
                ctx,
                artifact,
                artifact_digest="0" * 64,
                confirmed=True,
                approved_by="user",
            ),
        )
        assert forged.status_code == 409
        assert forged.json()["error"]["code"] == "CARD_DIGEST_MISMATCH"
        assert visual.submit_calls == []

        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["approvals"] == []
        assert view["generation_job"] is None


async def test_confirm_requires_approved_current_card_and_no_unresolved_questions(full_settings):
    """AC-21/G04：未审查卡或未解决问题不得进入确认。"""
    visual = VisualStub()
    registry = build_test_registry(visual=visual)
    async with _client_for(full_settings, registry) as (application, client):
        project_id, ctx, artifact = await _seed(application, client, approved=False)
        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(ctx, artifact),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ARTIFACT_NOT_APPROVED"
        assert visual.submit_calls == []

    async with _client_for(full_settings, build_test_registry(visual=visual)) as (application, client):
        project_id, ctx, artifact = await _seed(
            application,
            client,
            payload=_brief(unresolved=["还缺少目标平台尺寸"]),
        )
        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(ctx, artifact),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REQUIREMENTS_UNRESOLVED"
        assert visual.submit_calls == []


async def test_revise_requirements_invalidates_confirmation_and_reruns_s03(full_settings):
    """AC-21：需求编辑走显式 v2 动作，旧确认失效并交给 S03 重新审查。"""
    llm = LLMStub(role_responder=_revision_responder)
    visual = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    async with _client_for(full_settings, registry) as (application, client):
        project_id, ctx, artifact = await _seed(application, client)
        confirmed = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(ctx, artifact),
        )
        assert confirmed.status_code == 200, confirmed.text

        # 旧的 v1 写入入口在项目已有 WorkflowRun 时继续 fail closed。
        patch = await client.patch(
            f"/api/projects/{project_id}/brief",
            json={
                "expected_revision": ctx.project.revision,
                "product_description": "黑色帆布托特包",
            },
        )
        assert patch.status_code == 409, patch.text
        assert patch.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

        edit_text = "改成黑色帆布托特包，保留简洁暖色风格，图片文字去掉。"
        revised = await client.post(
            f"/api/projects/{project_id}/workflow/actions/revise_requirements",
            json={
                "expected_revision": ctx.project.revision,
                "text": edit_text,
                "payload": {},
            },
        )
        assert revised.status_code == 200, revised.text
        view = revised.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"
        assert view["project_revision"] == ctx.project.revision + 1
        assert view["requirement_revision"] == view["project_revision"]
        assert view["issue"] is None
        assert view["generation_job"] is None
        assert all(approval["revoked_at"] is not None for approval in view["approvals"])

        artifacts = view["artifacts"]
        assert any(
            item["kind"] == "final_brief" and item["status"] == "approved"
            for item in artifacts
        )
        drafts = [item for item in artifacts if item["kind"] == "brief_draft"]
        assert drafts and drafts[-1]["status"] == "approved"
        assert drafts[-1]["payload"]["product_description"] == "黑色帆布托特包"
        assert drafts[-1]["payload"]["texts"] == []

        roles = [
            request.payload["task"]["role"] for request in llm.role_calls_received
        ]
        assert roles[-2:] == ["requirements_consultant", "output_reviewer"]
        consultant = llm.role_calls_received[-2]
        assert consultant.payload["task"]["stage"] == "S03"
        assert consultant.payload["input_data"]["instruction"] == "revise_requirements"
        assert consultant.payload["input_data"]["current_requirement"]["artifact_id"] == artifact.id
        assert edit_text in consultant.payload["input_data"]["user_text"]
        assert visual.submit_calls == []

        project = (await client.get(f"/api/projects/{project_id}")).json()
        assert project["messages"][-1]["role"] == "user"
        assert project["messages"][-1]["content"] == edit_text

        # 旧卡不能借新版本重新确认，确认也不会触发任何生成。
        late = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json={**_action_body(ctx, artifact), "expected_revision": view["project_revision"]},
        )
        assert late.status_code == 409
        # 旧卡已经离开确认阶段，旧确认也不会被恢复。
        assert late.json()["error"]["code"] in {
            "ACTION_NOT_AVAILABLE",
            "REQUIREMENTS_CHANGED",
        }
        assert visual.submit_calls == []


async def test_confirm_never_resets_budget_or_advances_without_new_action(full_settings):
    """AC-21：确认不消耗/重置产物预算，重复确认不产生新的生成任务。"""
    visual = VisualStub()
    registry = build_test_registry(visual=visual)
    async with _client_for(full_settings, registry) as (application, client):
        project_id, ctx, artifact = await _seed(application, client)
        # 再生成一个候选后拒绝，确认不得复活它。
        rejected = await state.start_artifact(
            ctx,
            "brief_draft",
            _brief(product_description="旧候选"),
        )
        await state.add_review(
            ctx,
            rejected,
            reviewer_role="output_reviewer",
            decision="reject",
            checks=[{"rule_id": "T09_CARD", "outcome": "fail", "note": "旧候选"}],
        )
        response = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_action_body(ctx, artifact),
        )
        assert response.status_code == 200, response.text
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        rejected_views = [a for a in view["artifacts"] if a["kind"] == "brief_draft"]
        assert rejected_views and rejected_views[-1]["status"] == "rejected"
        assert view["generation_job"] is None
        assert visual.submit_calls == []
