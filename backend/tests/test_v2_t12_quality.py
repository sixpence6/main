from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from PIL import Image
from sqlalchemy import select

from app.db import read_session, write_transaction
from app.models import Asset, Project
from app.services.assets import inspect_image, write_asset_file
from app.timeutil import format_timestamp, utc_now
from app.workflow import state
from app.workflow.contracts import BriefData, ReferenceData
from app.workflow.tables import Approval, GenerationJob


@dataclass(frozen=True)
class Seed:
    project_id: str
    run_id: str
    asset_id: str
    job_id: str
    revision: int
    generated_bytes: bytes
    reference_bytes: bytes


def _png(size: tuple[int, int] = (64, 64), color: tuple[int, int, int] = (210, 220, 235)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _evidence_for(rule: dict[str, Any], allowed: list[str]) -> list[str]:
    evidence: list[str] = []
    for evidence_type in rule.get("evidence_types", []):
        match = next(
            (item for item in allowed if item.startswith(f"{evidence_type}:")), None
        )
        if match is not None:
            evidence.append(match)
    return evidence


def _quality_response(request: Any, mode: str = "pass") -> dict[str, Any]:
    payload = request.payload
    rules = payload["input_data"]["rules"]
    allowed = list(payload["task"]["allowed_evidence_ids"])
    checks: list[dict[str, Any]] = []
    decision = "pass"
    for rule in rules:
        if rule["applicability"] != "applicable":
            continue
        outcome = "pass"
        if mode == "fail" and rule["rule_id"] == "hard:text_logo":
            outcome = "fail"
            decision = "fail"
        elif mode == "unknown" and rule["rule_id"] == "hard:text_logo":
            outcome = "unknown"
            decision = "unknown"
        evidence = _evidence_for(rule, allowed)
        if mode == "bad_evidence" and rule["rule_id"] == "hard:subject":
            evidence = ["image:unknown-asset"]
        checks.append(
            {
                "rule_id": rule["rule_id"],
                "outcome": outcome,
                "evidence_ids": evidence,
                "note": "已核对",
            }
        )
    if mode == "missing_quality_rule":
        checks = [check for check in checks if check["rule_id"] != "hard:quantity"]
    summary = {
        "pass": "图片符合确认要求。",
        "fail": "图片文字不符合确认要求，本次不放行。",
        "unknown": "部分细节无法确认，本次不放行。",
    }.get(decision, "图片检查完成。")
    return {
        "role": "quality_compliance",
        "status": "ready",
        "data": {
            "asset_id": payload["input_data"]["asset_id"],
            "decision": decision,
            "checks": checks,
            "soft_feedback": [],
            "limitations": [],
            "user_summary": summary,
        },
        "issues": [],
        "questions": [],
    }


def _review_response(request: Any, mode: str = "pass") -> dict[str, Any]:
    targets = request.payload["review_targets"]
    allowed = list(request.payload["task"]["allowed_evidence_ids"])
    results: list[dict[str, Any]] = []
    for target in targets:
        checks: list[dict[str, Any]] = []
        target_decision = "pass"
        if target["target_kind"] == "quality_report":
            checks.append(
                {
                    "rule_id": "review:quality_report",
                    "outcome": "pass",
                    "evidence_ids": [f"report:{target['artifact_id']}"],
                    "note": "报告与本次检查一致",
                }
            )
        else:
            by_id = {rule["rule_id"]: rule for rule in target["rules"]}
            for rule_id in target["required_rule_ids"]:
                if mode == "missing_rule" and rule_id == "hard:forbidden":
                    continue
                rule = by_id[rule_id]
                outcome = "pass"
                if mode == "reviewer_reject" and rule_id == "hard:text_logo":
                    outcome = "fail"
                    target_decision = "reject"
                checks.append(
                    {
                        "rule_id": rule_id,
                        "outcome": outcome,
                        "evidence_ids": _evidence_for(rule, allowed),
                        "note": "已直接查看图片并核对" if outcome == "pass" else "文字与确认不一致",
                    }
                )
        results.append(
            {
                "target_id": target["target_id"],
                "decision": target_decision,
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


def _responder(mode: str):
    def respond(request: Any) -> dict[str, Any]:
        role = request.payload["task"]["role"]
        if role == "quality_compliance":
            return _quality_response(request, mode)
        if role == "output_reviewer":
            return _review_response(request, mode)
        raise AssertionError(f"unexpected role {role}")

    return respond


async def _seed_project(
    application: Any,
    settings: Any,
    client: Any,
    *,
    actual_size: tuple[int, int] = (64, 64),
    reference_usage: str = "product",
) -> Seed:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]

    reference_bytes = _png()
    reference_relative = f"references/{uuid4()}.png"
    write_asset_file(settings, reference_relative, reference_bytes)
    reference_info = inspect_image(reference_bytes)
    now = format_timestamp(utc_now())
    reference_id = str(uuid4())
    async with write_transaction(application.state.session_factory) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        session.add(
            Asset(
                id=reference_id,
                project_id=project_id,
                kind="reference",
                relative_path=reference_relative,
                mime_type=reference_info.mime_type,
                width=reference_info.width,
                height=reference_info.height,
                byte_size=len(reference_bytes),
                review_status="approved",
                created_at=now,
            )
        )
        await session.flush()
        project.brief_status = "confirmed"
        project.confirmed_revision = project.revision
        project.image_size = "64x64"
        project.reference_asset_id = reference_id
        project.product_description = "白色陶瓷马克杯"
        project.updated_at = now

    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx, "S09")
    brief = BriefData(
        product_description="白色陶瓷马克杯，容量 350ml",
        purpose="电商主图",
        facts=[],
        creative_direction="简洁棚拍",
        style_keywords=["clean"],
        composition="商品居中，留白充足",
        lighting="柔和棚拍光",
        texts=[],
        must_include=["商品主体完整"],
        must_preserve=["杯身颜色"],
        forbidden=["水印"],
        size="64x64",
        reference_asset_id=reference_id,
        reference_usage=reference_usage,
        assumptions=[],
        unresolved=[],
    )
    brief_artifact = await state.start_artifact(ctx, "final_brief", brief)
    await state.add_review(
        ctx,
        brief_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks=[
            {
                "rule_id": "review:final_brief",
                "outcome": "pass",
                "evidence_ids": [],
                "note": "需求卡通过",
            }
        ],
    )
    reference_data = ReferenceData(
        reference_usage=reference_usage,
        observations=[],
        texts=[],
        uncertainties=[],
    )
    reference_artifact = await state.start_artifact(ctx, "reference_report", reference_data)
    await state.add_review(
        ctx,
        reference_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks=[
            {
                "rule_id": "review:reference_report",
                "outcome": "pass",
                "evidence_ids": [],
                "note": "参考报告通过",
            }
        ],
    )
    snapshot_digest = await state.requirement_snapshot_digest(ctx)

    generated_bytes = _png(actual_size)
    generated_relative = f"generated/{uuid4()}.png"
    write_asset_file(settings, generated_relative, generated_bytes)
    generated_info = inspect_image(generated_bytes)
    asset_id = str(uuid4())
    job_id = str(uuid4())
    async with write_transaction(application.state.session_factory) as session:
        session.add(
            Asset(
                id=asset_id,
                project_id=project_id,
                kind="generated",
                relative_path=generated_relative,
                mime_type=generated_info.mime_type,
                width=generated_info.width,
                height=generated_info.height,
                byte_size=len(generated_bytes),
                review_status="quarantined",
                created_at=now,
            )
        )
        session.add(
            GenerationJob(
                id=job_id,
                project_id=project_id,
                run_id=ctx.run.id,
                request_id=str(uuid4()),
                request_digest="b" * 64,
                confirmed_snapshot_digest=snapshot_digest,
                status="quarantined",
                submit_attempted=0,
                result_asset_id=asset_id,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()

    return Seed(
        project_id=project_id,
        run_id=ctx.run.id,
        asset_id=asset_id,
        job_id=job_id,
        revision=ctx.project.revision,
        generated_bytes=generated_bytes,
        reference_bytes=reference_bytes,
    )


async def _post_review(client: Any, seed: Seed):
    return await client.post(
        f"/api/projects/{seed.project_id}/workflow/actions/review_candidate",
        json={"expected_revision": seed.revision},
    )


async def _load_asset(application: Any, asset_id: str) -> Asset:
    async with read_session(application.state.session_factory) as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        return asset


async def _load_job(application: Any, job_id: str) -> GenerationJob:
    async with read_session(application.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        assert job is not None
        return job


async def _approvals(application: Any, project_id: str) -> list[Approval]:
    async with read_session(application.state.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(Approval).where(Approval.project_id == project_id)
                )
            ).all()
        )


def _artifact_by_kind(body: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for item in body["artifacts"] if item["kind"] == kind)


async def test_quality_pass_records_separate_report_and_image_approvals(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("pass")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "S10"
    assert body["status"] == "active"
    report = _artifact_by_kind(body, "quality_report")
    assert report["status"] == "approved"
    assert any(item["approval_type"] == "image" for item in body["approvals"])

    calls = stub.role_calls_received
    assert [call.payload["task"]["role"] for call in calls] == [
        "quality_compliance",
        "output_reviewer",
    ]
    assert [image.data for image in calls[0].images] == [
        seed.generated_bytes,
        seed.reference_bytes,
    ]
    assert [image.data for image in calls[1].images] == [
        seed.generated_bytes,
        seed.reference_bytes,
    ]
    quality_input = calls[0].payload["input_data"]
    assert quality_input["asset_id"] == seed.asset_id
    assert quality_input["technical"]["size_matches"] is True
    assert "hard:product_consistency" in calls[0].payload["task"]["required_rule_ids"]
    targets = calls[1].payload["review_targets"]
    assert {target["target_kind"] for target in targets} == {"quality_report", "image"}
    report_target = next(target for target in targets if target["target_kind"] == "quality_report")
    image_target = next(target for target in targets if target["target_kind"] == "image")
    assert report_target["artifact_id"] == report["id"]
    assert report_target["artifact_id"] != seed.asset_id
    assert image_target["asset_id"] == seed.asset_id

    asset = await _load_asset(llm_app, seed.asset_id)
    job = await _load_job(llm_app, seed.job_id)
    assert asset.review_status == "approved"
    assert job.status == "approved"
    approvals = await _approvals(llm_app, seed.project_id)
    image_approval = next(item for item in approvals if item.approval_type == "image")
    checks = json.loads(image_approval.checks_json)
    assert checks["asset_id"] == seed.asset_id
    assert checks["quality_report_id"] == report["id"]
    assert image_approval.artifact_digest == report["digest"]


async def test_hard_failure_keeps_image_quarantined_but_report_may_pass(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("fail")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "S09"
    assert body["status"] == "blocked"
    assert body["issue"]["code"] == "IMAGE_NOT_APPROVED"
    report = _artifact_by_kind(body, "quality_report")
    assert report["status"] == "approved"
    assert not any(item["approval_type"] == "image" for item in body["approvals"])
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert (await _load_job(llm_app, seed.job_id)).status == "rejected"
    assert len(stub.role_calls_received) == 2

    retry = await _post_review(llm_client, seed)
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "ACTION_NOT_AVAILABLE"
    assert len(stub.role_calls_received) == 2


async def test_unknown_hard_rule_does_not_default_to_pass(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("unknown")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "S09"
    assert body["status"] == "blocked"
    assert _artifact_by_kind(body, "quality_report")["status"] == "approved"
    assert not any(item["approval_type"] == "image" for item in body["approvals"])
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert (await _load_job(llm_app, seed.job_id)).status == "quarantined"


async def test_contract_valid_wrong_evidence_is_rejected_before_review(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("bad_evidence")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "CONTRACT_ERROR"
    assert [call.payload["task"]["role"] for call in stub.role_calls_received] == [
        "quality_compliance"
    ]
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert not any(
        item.approval_type == "image"
        for item in await _approvals(llm_app, seed.project_id)
    )


async def test_local_technical_size_blocks_approval(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("pass")
    seed = await _seed_project(
        llm_app, llm_settings, llm_client, actual_size=(32, 32)
    )

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"
    assert stub.role_calls_received[0].payload["input_data"]["technical"][
        "size_matches"
    ] is False
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert not any(
        item.approval_type == "image"
        for item in await _approvals(llm_app, seed.project_id)
    )


async def test_reviewer_missing_required_image_rule_is_rejected(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("missing_rule")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "CONTRACT_ERROR"
    assert len(stub.role_calls_received) == 2
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert not any(
        item.approval_type == "image"
        for item in await _approvals(llm_app, seed.project_id)
    )


async def test_reviewer_hard_failure_blocks_image_without_contract_error(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("reviewer_reject")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "S09"
    assert body["status"] == "blocked"
    assert body["issue"]["code"] == "IMAGE_NOT_APPROVED"
    assert _artifact_by_kind(body, "quality_report")["status"] == "approved"
    assert not any(item["approval_type"] == "image" for item in body["approvals"])
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert (await _load_job(llm_app, seed.job_id)).status == "rejected"
    assert len(stub.role_calls_received) == 2


async def test_reviewer_unknown_rule_is_rejected(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm

    def respond(request: Any) -> dict[str, Any]:
        if request.payload["task"]["role"] == "quality_compliance":
            return _quality_response(request)
        data = _review_response(request)
        data["data"]["results"][0]["checks"].append(
            {
                "rule_id": "review:made_up",
                "outcome": "pass",
                "evidence_ids": [],
                "note": "unknown rule",
            }
        )
        return data

    stub.role_responder = respond
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "CONTRACT_ERROR"
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "quarantined"
    assert not any(
        item.approval_type == "image"
        for item in await _approvals(llm_app, seed.project_id)
    )


async def test_non_product_reference_does_not_claim_product_consistency(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("pass")
    seed = await _seed_project(
        llm_app, llm_settings, llm_client, reference_usage="style"
    )

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "S10"
    assert "hard:product_consistency" not in stub.role_calls_received[0].payload[
        "task"
    ]["required_rule_ids"]
    assert (await _load_asset(llm_app, seed.asset_id)).review_status == "approved"
