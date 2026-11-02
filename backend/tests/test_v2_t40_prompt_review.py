from __future__ import annotations

import io
from contextlib import asynccontextmanager

import pytest
from httpx import AsyncClient
from PIL import Image

from app.adapters.base import ProviderError
from app.config import Settings
from app.db import write_transaction
from app.main import create_app
from app.models import Project
from app.workflow import state
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate
from tests.support.auth import authenticated_client

REVIEW_RULES = ("G03_CONFIRMATION", "G05_EXECUTION_BOUNDARY")


def png_bytes(width: int = 640, height: int = 480, color=(230, 230, 230)) -> bytes:
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


def expected_rule_ids(brief: dict) -> set[str]:
    ids = {
        "hard:subject",
        "hard:purpose",
        "hard:creative",
        "hard:style",
        "hard:composition",
        "hard:lighting",
        "hard:size",
        "hard:count",
    }
    for index in range(len(brief.get("texts") or [])):
        ids.add(f"hard:text:{index}")
    for index in range(len(brief.get("facts") or [])):
        ids.add(f"hard:fact:{index}")
    for index in range(len(brief.get("must_include") or [])):
        ids.add(f"hard:must_include:{index}")
    for index in range(len(brief.get("must_preserve") or [])):
        ids.add(f"hard:must_preserve:{index}")
    for index in range(len(brief.get("forbidden") or [])):
        ids.add(f"hard:forbidden:{index}")
    if brief.get("reference_asset_id"):
        ids.add("hard:reference_asset_id")
    return ids


def candidate_coverage(brief: dict, reference_id: str | None) -> list[dict]:
    values = [
        ("hard:subject", "positive_prompt", brief["product_description"]),
        ("hard:purpose", "positive_prompt", brief["purpose"]),
        ("hard:creative", "positive_prompt", brief["creative_direction"]),
        ("hard:style", "positive_prompt", "、".join(brief["style_keywords"])),
        ("hard:composition", "positive_prompt", brief["composition"]),
        ("hard:lighting", "positive_prompt", brief["lighting"]),
    ]
    for index, text in enumerate(brief.get("texts") or []):
        values.append((f"hard:text:{index}", "positive_prompt", text))
    for index, fact in enumerate(brief.get("facts") or []):
        values.append((f"hard:fact:{index}", "positive_prompt", fact["text"]))
    for index, value in enumerate(brief.get("must_include") or []):
        values.append((f"hard:must_include:{index}", "positive_prompt", value))
    for index, value in enumerate(brief.get("must_preserve") or []):
        values.append((f"hard:must_preserve:{index}", "positive_prompt", value))
    for index, value in enumerate(brief.get("forbidden") or []):
        values.append((f"hard:forbidden:{index}", "negative_prompt", value))
    values.append(("hard:size", "size", brief["size"]))
    values.append(("hard:count", "count", "1"))
    if reference_id:
        values.append(("hard:reference_asset_id", "reference_asset_id", reference_id))
    return [
        {"rule_id": rule_id, "binding": binding, "value": value}
        for rule_id, binding, value in values
    ]


def candidate_data(
    brief: dict,
    *,
    omit: str | None = None,
    duplicate: str | None = None,
    fake: bool = False,
    size: str | None = None,
    count: int = 1,
    reference_id: object = ...,
    extra_positive: str | None = None,
    count_binding: str | None = None,
) -> dict:
    resolved_reference = brief.get("reference_asset_id") if reference_id is ... else reference_id
    if resolved_reference is None:
        resolved_reference = None
    positive_parts = [
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
    if resolved_reference:
        positive_parts.append("参考图仅用于商品外观，不复制背景")
    if extra_positive:
        positive_parts.append(extra_positive)
    coverage = candidate_coverage(brief, resolved_reference)
    if omit:
        coverage = [item for item in coverage if item["rule_id"] != omit]
    if duplicate:
        duplicate_item = next(item for item in coverage if item["rule_id"] == duplicate)
        coverage.append(dict(duplicate_item))
    if fake:
        coverage[0]["value"] = "伪造片段"
    if count_binding is not None:
        next(item for item in coverage if item["rule_id"] == "hard:count")["value"] = count_binding
    return {
        "positive_prompt": "；".join(positive_parts),
        "negative_prompt": "；".join(brief["forbidden"]),
        "size": size or brief["size"],
        "count": count,
        "reference_asset_id": resolved_reference,
        "requirement_coverage": coverage,
    }


def engineer_response(data: dict) -> dict:
    return {
        "role": "generation_engineer",
        "status": "ready",
        "data": data,
        "issues": [],
        "questions": [],
    }


def review_response(
    target_id: str,
    *,
    decision: str = "pass",
    rules: tuple[str, ...] = REVIEW_RULES,
    evidence: list[str] | None = None,
    duplicate_rule: str | None = None,
) -> dict:
    outcome = "pass" if decision == "pass" else "fail"
    checks = [
        {
            "rule_id": rule_id,
            "outcome": outcome,
            "evidence_ids": evidence if evidence is not None else [target_id],
            "note": "仅按已确认快照检查提示词编制结果",
        }
        for rule_id in rules
    ]
    if duplicate_rule:
        checks.append(dict(next(check for check in checks if check["rule_id"] == duplicate_rule)))
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {"target_id": target_id, "decision": decision, "checks": checks}
            ]
        },
        "issues": [],
        "questions": [],
    }


@asynccontextmanager
async def environment(
    settings: Settings,
    *,
    engineer,
    reviewer,
):
    llm = LLMStub()
    visual = VisualStub()

    def responder(request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer":
            return engineer(request)
        if role == "output_reviewer":
            return reviewer(request)
        raise AssertionError(f"unexpected role {role}")

    llm.role_responder = responder
    registry = build_test_registry(llm=llm, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, llm, visual, registry


async def setup_confirmed_project(
    app,
    client: AsyncClient,
    *,
    with_reference: bool = False,
    image_size: str = "1024x1024",
    brief_override: dict | None = None,
) -> dict:
    created_response = await client.post("/api/projects", json={})
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    project_id = created["id"]
    revision = created["revision"]
    reference_id = None
    if with_reference:
        uploaded_response = await client.post(
            f"/api/projects/{project_id}/reference",
            files={"file": ("reference.png", png_bytes(), "image/png")},
            data={"expected_revision": str(revision)},
        )
        assert uploaded_response.status_code == 200, uploaded_response.text
        uploaded = uploaded_response.json()
        revision = uploaded["revision"]
        reference_id = uploaded["reference_asset"]["id"]

    brief = dict(brief_override) if brief_override is not None else base_brief(reference_id)
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
    await state.create_run(ctx, stage="S07")
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
    return {
        "project_id": project_id,
        "revision": revision,
        "brief": brief,
        "reference_id": reference_id,
        "final_artifact_id": approved_final.id,
    }


async def workflow_action(
    client: AsyncClient,
    setup: dict,
    action: str,
    *,
    text: str | None = None,
):
    body = {"expected_revision": setup["revision"]}
    if text is not None:
        body["text"] = text
    return await client.post(
        f"/api/projects/{setup['project_id']}/workflow/actions/{action}",
        json=body,
    )


@pytest.mark.asyncio
async def test_compile_prompt_builds_only_confirmed_candidate_and_gets_independent_review(full_settings):
    brief = base_brief()
    engineer_data = candidate_data(brief)
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(engineer_data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S08"
        assert view["status"] == "active"
        assert len(llm.role_calls_received) == 2
        assert [request.payload["task"]["role"] for request in llm.role_calls_received] == [
            "generation_engineer",
            "output_reviewer",
        ]
        engineer_request = llm.role_calls_received[0]
        provided = engineer_request.payload["input_data"]["hard_requirements"]
        assert {item["rule_id"] for item in provided} == expected_rule_ids(brief)
        assert engineer_request.payload["state"]["brief_confirmed"] is True
        assert engineer_request.payload["capabilities"] == {"size": "1024x1024", "count": 1}
        assert engineer_request.payload["approved_inputs"]["final_brief"]["product_description"] == brief["product_description"]
        reviewer_request = llm.role_calls_received[1]
        target_id = reviewer_request.payload["review_targets"][0]["target_id"]
        assert target_id == next(
            artifact["id"] for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan"
        )
        prompt = next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")
        assert prompt["status"] == "approved"
        assert prompt["payload"]["positive_prompt"] == engineer_data["positive_prompt"]
        assert prompt["payload"]["negative_prompt"] == "；".join(brief["forbidden"])
        assert prompt["payload"]["size"] == "1024x1024"
        assert prompt["payload"]["count"] == 1
        assert prompt["payload"]["requirement_coverage"] == engineer_data["requirement_coverage"]
        approvals = [item for item in view["approvals"] if item["approval_type"] == "generation_prompt"]
        assert len(approvals) == 1
        assert approvals[0]["approved_by"] == "output_reviewer"
        assert not any(item["approved_by"] == "user" and item["approval_type"] == "generation_prompt" for item in view["approvals"])
        assert visual.submit_calls == []
        assert _registry.recorder.visual_builds == 0
        assert "WARM MORNING" in prompt["payload"]["positive_prompt"]
        assert "价格标签" in prompt["payload"]["negative_prompt"]


@pytest.mark.asyncio
async def test_natural_prompt_wording_keeps_confirmed_requirement_coverage(full_settings):
    brief = base_brief()
    data = candidate_data(brief)
    data["positive_prompt"] = (
        data["positive_prompt"]
        .replace("主体居中，留白充足", "主体居中，四周留白充足")
        .replace("杯口金色描边", "杯口保留金色描边")
    )
    rules = (*REVIEW_RULES, *sorted(expected_rule_ids(brief)))
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"], rules=rules
        ),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        view = response.json()
        prompt = next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")
        assert prompt["status"] == "approved"
        assert "主体居中，四周留白充足" in prompt["payload"]["positive_prompt"]
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_multi_clause_requirement_coverage_is_accepted(full_settings):
    brief = base_brief()
    brief["purpose"] = "电商主图；用于大屏展示"
    data = candidate_data(brief)
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, _llm, _visual, _registry):
        setup = await setup_confirmed_project(app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        view = response.json()
        prompt = next(
            artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan"
        )
        assert prompt["status"] == "approved"
        assert "电商主图；用于大屏展示" in prompt["payload"]["positive_prompt"]


@pytest.mark.asyncio
async def test_nested_issues_questions_are_hoisted_before_reviewer_validation(full_settings):
    brief = base_brief()
    data = candidate_data(brief)
    rules = (*REVIEW_RULES, *sorted(expected_rule_ids(brief)))

    def reviewer(request):
        response = review_response(
            request.payload["review_targets"][0]["target_id"], rules=rules
        )
        response["data"]["issues"] = response.pop("issues")
        response["data"]["questions"] = response.pop("questions")
        return response

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=reviewer,
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        view = response.json()
        prompt = next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")
        assert prompt["status"] == "approved"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_removed_requirement_text_still_blocks_prompt(full_settings):
    brief = base_brief()
    data = candidate_data(brief)
    data["positive_prompt"] = data["positive_prompt"].replace(
        "杯口金色描边", "杯口描边"
    )
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_compile_prompt_keeps_original_reference_id_and_explicit_usage(full_settings):
    async with environment(
        full_settings,
        engineer=lambda request: engineer_response(
            candidate_data(request.payload["approved_inputs"]["final_brief"], reference_id=request.payload["approved_inputs"]["final_brief"]["reference_asset_id"])
        ),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client, with_reference=True)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        view = response.json()
        prompt = next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")
        assert prompt["payload"]["reference_asset_id"] == setup["reference_id"]
        assert "参考图仅用于商品外观，不复制背景" in prompt["payload"]["positive_prompt"]
        coverage = prompt["payload"]["requirement_coverage"]
        reference_coverage = next(item for item in coverage if item["rule_id"] == "hard:reference_asset_id")
        assert reference_coverage == {
            "rule_id": "hard:reference_asset_id",
            "binding": "reference_asset_id",
            "value": setup["reference_id"],
        }
        assert visual.submit_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("omit", ["hard:subject", "hard:purpose"])
async def test_omitted_hard_requirement_blocks_before_any_submission(full_settings, omit: str):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief, omit=omit)),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert all(artifact["kind"] != "prompt_plan" for artifact in view["artifacts"])


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_reviewer_may_group_checks_under_one_required_rule(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"],
            duplicate_rule="G03_CONFIRMATION",
        ),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")

        assert response.status_code == 200
        view = response.json()
        assert view["stage"] == "S08"
        assert any(item["kind"] == "prompt_plan" and item["status"] == "approved" for item in view["artifacts"])


async def test_duplicate_requirement_coverage_blocks_before_any_submission(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief, duplicate="hard:subject")),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_fake_prompt_binding_blocks_before_any_submission(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief, fake=True)),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_confirmed_fact_drift_blocks_before_any_submission(full_settings):
    brief = base_brief()
    data = candidate_data(brief)
    data["positive_prompt"] = data["positive_prompt"].replace(
        "杯身印有蓝色鲸鱼图案", "杯身印有红色海豚图案"
    )
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert all(artifact["kind"] != "prompt_plan" for artifact in view["artifacts"])


@pytest.mark.asyncio
async def test_added_unconfirmed_selling_point_is_rejected_by_independent_review(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief, extra_positive="限时特价 9.9 元")),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"], decision="reject"
        ),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_REVIEW_REJECTED"
        assert len(llm.role_calls_received) == 2
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        prompt = next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")
        assert prompt["status"] == "rejected"
        assert prompt["payload"] is None
        assert not any(item["approval_type"] == "generation_prompt" for item in view["approvals"])


@pytest.mark.asyncio
async def test_reference_id_size_and_count_mismatches_block_before_any_submission(full_settings):
    brief = base_brief()
    cases = [
        ("reference_id_mismatch", {"reference_id": "wrong-reference-id"}, 409, "PROMPT_INVALID"),
        ("size_mismatch", {"size": "1536x1536"}, 409, "PROMPT_INVALID"),
        ("count_mismatch", {"count": 2}, 503, "SERVICE_UNAVAILABLE"),
        ("count_binding_mismatch", {"count_binding": "2"}, 409, "PROMPT_INVALID"),
    ]
    for _name, overrides, expected_status, expected_code in cases:
        async with environment(
            full_settings,
            engineer=lambda _request, overrides=overrides: engineer_response(candidate_data(brief, **overrides)),
            reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
        ) as (app, client, llm, visual, _registry):
            setup = await setup_confirmed_project(app, client)
            response = await workflow_action(client, setup, "compile_prompt")
            assert response.status_code == expected_status, response.text
            assert response.json()["error"]["code"] == expected_code
            assert len(llm.role_calls_received) == 1
            assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_wrong_reviewer_rules_or_evidence_never_approve_prompt(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"], rules=("WRONG_RULE",)
        ),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_REVIEW_INVALID"
        assert len(llm.role_calls_received) == 2
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert not any(item["approval_type"] == "generation_prompt" for item in view["approvals"])
        prompt = next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")
        assert prompt["status"] == "candidate"


@pytest.mark.asyncio
async def test_input_change_after_approval_revokes_generation_prompt_submission_right(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        approved = await workflow_action(client, setup, "compile_prompt")
        assert approved.status_code == 200, approved.text
        assert any(
            item["approval_type"] == "generation_prompt" and item["revoked_at"] is None
            for item in approved.json()["approvals"]
        )

        ctx = await state.load_context(app, setup["project_id"])
        await state.invalidate_requirements(ctx, "用户在批准提示词后修改了需求")

        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        prompt_approvals = [
            item for item in view["approvals"] if item["approval_type"] == "generation_prompt"
        ]
        assert prompt_approvals and all(item["revoked_at"] is not None for item in prompt_approvals)
        assert visual.submit_calls == []
        assert _registry.recorder.visual_builds == 0

@pytest.mark.asyncio
async def test_old_requirements_confirmation_blocks_compile_before_model_call(full_settings):
    brief = base_brief()
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        ctx = await state.load_context(app, setup["project_id"])
        await state.invalidate_requirements(ctx, "需求版本已变化")
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONFIRM_REQUIRED"
        assert llm.role_calls_received == []
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_technical_failure_makes_one_attempt_and_no_automatic_retry(full_settings):

    def provider_failure(_request):
        return ProviderError("模拟提供方失败", evidence="not_submitted", error_code="LLM_UNAVAILABLE")

    async with environment(
        full_settings,
        engineer=provider_failure,
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["status"] == "failed"


@pytest.mark.asyncio
async def test_revision_budget_is_three_candidates_and_fourth_is_blocked(full_settings):
    brief = base_brief()

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"], decision="reject"
        ),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        first = await workflow_action(client, setup, "compile_prompt")
        assert first.status_code == 409, first.text
        for index in range(2):
            response = await workflow_action(client, setup, "revise_prompt", text=f"第 {index + 2} 次修正")
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "PROMPT_REVIEW_REJECTED"
        fourth = await workflow_action(client, setup, "revise_prompt", text="第四次修正")
        assert fourth.status_code == 409, fourth.text
        assert fourth.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        assert len(llm.role_calls_received) == 6
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        prompt_artifacts = [item for item in view["artifacts"] if item["kind"] == "prompt_plan"]
        assert len(prompt_artifacts) == 3


@pytest.mark.asyncio
async def test_model_cannot_smuggle_provider_or_model_choice_into_candidate(full_settings):
    brief = base_brief()
    data = candidate_data(brief)
    data["provider"] = "evil-provider"
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []
