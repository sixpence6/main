from __future__ import annotations

import asyncio
import json
import types
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.db import read_session, write_transaction
from app.models import Project
from app.workflow import state
from app.workflow.stages import s05
from app.workflow.tables import Approval, Artifact, RoleCall
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import _drain, _environment, _responder
from tests.test_v2_t40_prompt_review import base_brief, png_bytes
from tests.test_v3_p1_t04_production_reviews import creative_payload, visual_payload

_DEDICATED = {
    "background": "浅灰无缝背景",
    "palette": ["白色", "浅灰"],
    "text_layout": "主标题置于画面下方居中",
    "reference_treatment": "无参考图，按确认需求构图",
    "preservation_rules": ["杯口金色描边"],
    "copy_lines": ["WARM MORNING"],
}
_SOURCES = {
    "product_description": "brief_draft",
    "purpose": "brief_draft",
    "facts": "brief_draft",
    "creative_direction": "creative_direction",
    "style_keywords": "visual_plan",
    "composition": "visual_plan",
    "lighting": "visual_plan",
    "texts": "creative_direction",
    "must_include": "brief_draft",
    "must_preserve": "brief_draft+visual_plan",
    "forbidden": "brief_draft",
    "size": "brief_draft",
    "reference_asset_id": "brief_draft",
    "reference_usage": "brief_draft",
    "assumptions": "brief_draft",
    "unresolved": "brief_draft",
    "background": "visual_plan",
    "palette": "visual_plan",
    "text_layout": "visual_plan",
    "reference_treatment": "visual_plan",
    "preservation_rules": "visual_plan",
    "copy_lines": "creative_direction",
}


def _execution_brief() -> dict[str, Any]:
    brief = base_brief(None)
    brief.update(_DEDICATED)
    brief["field_sources"] = dict(_SOURCES)
    return brief


async def _setup_execution_snapshot(app: Any, client: AsyncClient) -> dict[str, Any]:
    created_response = await client.post("/api/projects", json={})
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    project_id = created["id"]
    revision = created["revision"] + 1
    brief = _execution_brief()
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


def _confirmation(view: dict[str, Any]) -> dict[str, Any]:
    approvals = [
        item
        for item in view["approvals"]
        if item["approval_type"] == "requirements_card" and item["revoked_at"] is None
    ]
    assert len(approvals) == 1
    confirmation = approvals[0]["checks"].get("confirmation")
    assert isinstance(confirmation, dict)
    return confirmation


def _fields(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in snapshot["fields"]}


def test_s05_composer_keeps_dedicated_visual_and_creative_fields() -> None:
    ctx = types.SimpleNamespace(
        project=types.SimpleNamespace(image_size="1024x1024"),
        settings=types.SimpleNamespace(
            visual=lambda: types.SimpleNamespace(image_size="1024x1024")
        ),
    )
    brief = base_brief(None)
    payload = s05._compose_final_brief(ctx, brief, creative_payload(), visual_payload())

    assert payload["background"] == "浅灰无缝背景"
    assert payload["palette"] == ["白色", "浅灰"]
    assert payload["text_layout"] == "画面无文字"
    assert payload["reference_treatment"] == "无参考图，按确认需求构图"
    assert payload["preservation_rules"] == ["一只杯子"]
    assert payload["copy_lines"] == []
    assert payload["field_sources"]["background"] == "visual_plan"
    assert payload["field_sources"]["composition"] == "visual_plan"
    assert payload["field_sources"]["copy_lines"] == "creative_direction"


@pytest.mark.asyncio
async def test_execution_snapshot_flows_confirm_compile_qc_and_history(full_settings) -> None:
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    request_id = str(uuid.uuid4())
    async with _environment(full_settings, llm, visual, _responder) as (app, client):
        setup = await _setup_execution_snapshot(app, client)
        confirmed = await client.post(
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
        assert confirmed.status_code == 200, confirmed.text
        view = confirmed.json()
        assert view["stage"] == "S07"
        snapshot = _confirmation(view)
        digest = snapshot["snapshot_digest"]
        assert len(digest) == 64
        assert snapshot["snapshot_schema_version"] == "1.0.0"
        assert snapshot["contract_version"] == "1.0.0"
        assert snapshot["rules_version"] == "v3.rules.1"
        assert snapshot["snapshot_encoding"] == "canonical-json-sha256-v1"
        assert snapshot["artifact_digest"] == setup["final_digest"]

        fields = _fields(snapshot)
        for path, value in _DEDICATED.items():
            assert fields[path]["value"] == value
            assert fields[path]["source"] == (
                "creative_direction" if path == "copy_lines" else "visual_plan"
            )
            assert fields[path]["executable"] is True
        assert fields["background"]["strength"] == "soft"
        assert fields["background"]["user_lock_basis"] is None
        assert fields["must_include"]["strength"] == "hard"
        assert fields["must_include"]["original_value"] == ["杯身蓝色鲸鱼图案"]
        assert fields["must_include"]["constraint_id"].startswith("req:must_include:")
        assert fields["assumptions"]["executable"] is False
        assert fields["assumptions"]["reason"]

        replay = await client.post(
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
        assert replay.status_code == 200, replay.text
        replay_snapshot = _confirmation(replay.json())
        assert replay_snapshot["snapshot_digest"] == digest

        await _drain(app)
        engineer = [
            call for call in llm.role_calls_received
            if call.payload["task"]["role"] == "generation_engineer"
        ][0]
        approved_inputs = engineer.payload["approved_inputs"]
        assert approved_inputs["final_brief"]["background"] == "浅灰无缝背景"
        assert approved_inputs["execution_snapshot"]["snapshot_digest"] == digest
        assert approved_inputs["execution_snapshot"]["fields"] == snapshot["fields"]

        quality = [
            call for call in llm.role_calls_received
            if call.payload["task"]["role"] == "quality_compliance"
        ][0]
        quality_input = quality.payload["input_data"]
        assert quality_input["approved_inputs"]["execution_snapshot"]["snapshot_digest"] == digest
        assert quality_input["approved_inputs"]["final_brief"]["background"] == "浅灰无缝背景"
        composition_rule = next(
            rule for rule in quality_input["rules"] if rule["rule_id"] == "hard:composition"
        )
        for value in (
            "浅灰无缝背景",
            "白色、浅灰",
            "主标题置于画面下方居中",
            "无参考图，按确认需求构图",
        ):
            assert value in composition_rule["requirement"]

        preparing = (await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )).json()
        assert preparing["stage"] == "S10"
        approved = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/approve_delivery",
            json={"expected_revision": preparing["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        ready = approved.json()
        published = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/publish_delivery",
            json={"expected_revision": ready["project_revision"]},
        )
        assert published.status_code == 200, published.text

        workflow = (await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )).json()
        version = workflow["versions"][0]
        assert version["execution_snapshot_digest"] == digest
        assert version["snapshot_schema_version"] == "1.0.0"
        assert version["contract_version"] == "1.0.0"
        assert version["brief"]["background"] == "浅灰无缝背景"
        assert version["execution_snapshot"]["snapshot_digest"] == digest
        assert version["execution_snapshot"]["fields"] == snapshot["fields"]

        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None
            project.product_description = "后来修改的商品"
            project.style = "后来修改的风格"
            project.revision += 1
            await session.flush()

        history = (await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )).json()["versions"][0]
        assert history["brief"]["product_description"] == "磨砂玻璃咖啡杯，容量 350 毫升"
        assert history["brief"]["background"] == "浅灰无缝背景"
        assert history["execution_snapshot_digest"] == digest

        async with read_session(app.state.session_factory) as session:
            approvals = list(
                (
                    await session.scalars(
                        select(Approval).where(
                            Approval.project_id == setup["project_id"],
                            Approval.approval_type == "requirements_card",
                        )
                    )
                ).all()
            )
            artifacts = list(
                (
                    await session.scalars(
                        select(Artifact).where(
                            Artifact.project_id == setup["project_id"],
                            Artifact.kind == "final_brief",
                        )
                    )
                ).all()
            )
        assert len(approvals) == 1
        stored = json.loads(approvals[0].checks_json)["confirmation"]
        assert stored["snapshot_digest"] == digest
        assert stored["artifact_digest"] == artifacts[0].digest
        assert stored["snapshot_digest"] != stored["artifact_digest"]


@pytest.mark.asyncio
async def test_canonical_snapshot_digest_is_stable_for_same_inputs() -> None:
    from app.workflow.snapshot import build_execution_snapshot

    brief = _execution_brief()
    first = build_execution_snapshot(
        artifact_id="artifact-1",
        artifact_digest="a" * 64,
        artifact_revision_no=1,
        project_revision=2,
        requirement_revision=2,
        reference_asset_id=None,
        reference_usage="none",
        referenced_material_digest="b" * 64,
        brief=brief,
    )
    second = build_execution_snapshot(
        artifact_id="artifact-1",
        artifact_digest="a" * 64,
        artifact_revision_no=1,
        project_revision=2,
        requirement_revision=2,
        reference_asset_id=None,
        reference_usage="none",
        referenced_material_digest="b" * 64,
        brief=brief,
    )
    assert first["snapshot_digest"] == second["snapshot_digest"]
    changed = dict(brief)
    changed["background"] = "另一背景"
    third = build_execution_snapshot(
        artifact_id="artifact-1",
        artifact_digest="a" * 64,
        artifact_revision_no=1,
        project_revision=2,
        requirement_revision=2,
        reference_asset_id=None,
        reference_usage="none",
        referenced_material_digest="b" * 64,
        brief=changed,
    )
    assert third["snapshot_digest"] != first["snapshot_digest"]
