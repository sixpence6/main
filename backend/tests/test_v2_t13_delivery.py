from __future__ import annotations

import hashlib
import io
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.main import create_app
from app.models import Asset, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow.contracts import digest_payload
from app.workflow.stages import s10
from app.workflow.tables import Approval, Artifact, Delivery, GenerationJob, WorkflowRun
from tests.conftest import migrate
from tests.support.adapters import LLMStub, build_test_registry
from tests.support.auth import admin_account_id_for_seed, authenticated_client, ensure_test_admin


def _png_bytes(color: tuple[int, int, int] = (20, 80, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _brief_snapshot() -> dict[str, str]:
    return {
        "product_description": "米色托特包",
        "purpose": "电商主图",
        "style": "简洁棚拍",
        "composition": "正面居中",
        "text_content": "无文字",
    }


def _requirement_digest() -> str:
    return digest_payload(_brief_snapshot())


@dataclass(frozen=True)
class SeedIds:
    project_id: str
    run_id: str
    job_id: str
    asset_id: str
    approval_id: str | None
    digest: str
    image_bytes: bytes


@dataclass(frozen=True)
class DeliveryEnv:
    client: AsyncClient
    app: Any
    settings: Any
    stub: LLMStub
    ids: SeedIds


async def _seed_ready_state(
    settings: Any,
    session: Any,
    *,
    with_image_approval: bool = True,
    approval_rules: str = "2.0.0",
    approval_requirement_digest: str | None = None,
    asset_digest_override: str | None = None,
    write_file: bool = True,
    job_status: str = "approved",
    snapshot_artifact: dict[str, str] | None = None,
) -> SeedIds:
    project_id = str(uuid4())
    run_id = str(uuid4())
    job_id = str(uuid4())
    asset_id = str(uuid4())
    now = format_timestamp(utc_now())
    owner_account_id = await admin_account_id_for_seed(session)
    image_bytes = _png_bytes()
    digest = hashlib.sha256(image_bytes).hexdigest()
    relative_path = f"generated/{asset_id}.png"
    if write_file:
        target = settings.resolved_data_dir() / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image_bytes)

    snapshot = snapshot_artifact or _brief_snapshot()
    confirmed_digest = digest_payload(snapshot)
    project = Project(
        id=project_id,
        owner_account_id=owner_account_id,
        title="T13 交付测试项目",
        revision=1,
        prompt_revision=1,
        confirmed_revision=1,
        brief_status="confirmed",
        image_size="8x8",
        prompt="商品主图提示词",
        created_at=now,
        updated_at=now,
        **snapshot,
    )
    session.add(project)
    await session.flush()
    session.add(
        WorkflowRun(
            id=run_id,
            project_id=project_id,
            sequence_no=1,
            stage="S10",
            status="active",
            requirement_revision=1,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    if snapshot_artifact is not None:
        session.add(
            Artifact(
                id=str(uuid4()),
                run_id=run_id,
                project_id=project_id,
                kind="final_brief",
                revision_no=1,
                status="approved",
                payload_json=json.dumps(
                    snapshot_artifact,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                digest=confirmed_digest,
                created_at=now,
            )
        )
        await session.flush()
    session.add(
        Asset(
            id=asset_id,
            project_id=project_id,
            kind="generated",
            relative_path=relative_path,
            mime_type="image/png",
            width=8,
            height=8,
            byte_size=len(image_bytes),
            sha256=asset_digest_override or digest,
            review_status="approved",
            created_at=now,
        )
    )
    await session.flush()
    approval_id: str | None = None
    if with_image_approval:
        approval_id = str(uuid4())
        session.add(
            Approval(
                id=approval_id,
                project_id=project_id,
                artifact_id=None,
                approval_type="image",
                artifact_digest="image-artifact-digest",
                approved_by="output_reviewer",
                requirement_snapshot_digest=(approval_requirement_digest or confirmed_digest),
                referenced_material_digest="material-digest",
                prompt_version="2.0.0",
                rules_version=approval_rules,
                model_fingerprint="stub-llm:image:deadbeef",
                checks_json=json.dumps(
                    {"asset_id": asset_id, "asset_digest": digest},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
    await session.flush()
    session.add(
        GenerationJob(
            id=job_id,
            project_id=project_id,
            run_id=run_id,
            generation_id=None,
            request_id=str(uuid4()),
            request_digest="request-digest",
            prompt_artifact_id=None,
            confirmed_snapshot_digest=confirmed_digest,
            status=job_status,
            submit_attempted=1,
            remote_task_id="remote-task-not-public",
            result_asset_id=asset_id,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()
    return SeedIds(
        project_id=project_id,
        run_id=run_id,
        job_id=job_id,
        asset_id=asset_id,
        approval_id=approval_id,
        digest=digest,
        image_bytes=image_bytes,
    )


def _role_responder(
    ids: SeedIds,
    *,
    asset_id: str | None = None,
    requirement_revision: int = 1,
    filename_hint: str = "product-main.png",
    caption: str = "已保留基础检查限制，请以实际图片为准。",
):
    def responder(request: Any) -> dict[str, Any]:
        task = request.payload.get("task", {})
        role = task.get("role")
        if role == "delivery_manager":
            return {
                "role": "delivery_manager",
                "status": "ready",
                "data": {
                    "asset_id": asset_id or ids.asset_id,
                    "requirement_revision": requirement_revision,
                    "filename_hint": filename_hint,
                    "caption": caption,
                },
                "issues": [],
                "questions": [],
            }
        if role == "output_reviewer":
            targets = request.payload.get("review_targets") or []
            target_id = str(targets[0]["target_id"])
            allowed = list(task.get("allowed_evidence_ids") or [])
            evidence = allowed[:1] or [target_id]
            checks = [
                {
                    "rule_id": rule_id,
                    "outcome": "pass",
                    "evidence_ids": evidence,
                    "note": "已核对交付证据",
                }
                for rule_id in task.get("required_rule_ids") or []
            ]
            return {
                "role": "output_reviewer",
                "status": "ready",
                "data": {
                    "results": [
                        {
                            "target_id": target_id,
                            "decision": "pass",
                            "checks": checks,
                        }
                    ]
                },
                "issues": [],
                "questions": [],
            }
        raise AssertionError(f"unexpected role {role!r}")

    return responder


@asynccontextmanager
async def _delivery_env(
    settings: Any,
    stub: LLMStub,
    seed: Callable[[Any, Any], Any],
) -> AsyncIterator[DeliveryEnv]:
    migrate(settings)
    registry = build_test_registry(llm=stub)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        async with application.state.session_factory() as session:
            ids = await seed(settings, session)
            await session.commit()
        async with authenticated_client(application) as client:
            yield DeliveryEnv(
                client=client,
                app=application,
                settings=settings,
                stub=stub,
                ids=ids,
            )


async def _post_action(
    env: DeliveryEnv, action: str, *, revision: int = 1, payload: dict | None = None
) -> Any:
    return await env.client.post(
        f"/api/projects/{env.ids.project_id}/workflow/actions/{action}",
        json={"expected_revision": revision, "payload": payload or {}},
    )


async def _prepare(env: DeliveryEnv) -> dict[str, Any]:
    response = await _post_action(env, "prepare_delivery")
    assert response.status_code == 200, response.text
    return response.json()


async def _approve(env: DeliveryEnv) -> dict[str, Any]:
    response = await _post_action(env, "approve_delivery")
    assert response.status_code == 200, response.text
    return response.json()


async def _publish(env: DeliveryEnv) -> dict[str, Any]:
    response = await _post_action(env, "publish_delivery")
    assert response.status_code == 200, response.text
    return response.json()


async def test_delivery_is_reviewed_atomically_and_only_published_asset_is_readable(full_settings):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        before = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert before.status_code == 409, before.text
        assert env.ids.image_bytes not in before.content
        assert str(full_settings.resolved_data_dir()) not in before.text

        prepared = await _prepare(env)
        assert prepared["stage"] == "S10"
        assert prepared["status"] == "needs_input"
        assert prepared["issue"]["code"] == "DELIVERY_MANIFEST_READY"
        manifest = next(
            item for item in prepared["artifacts"] if item["kind"] == "delivery_manifest"
        )
        assert manifest["status"] == "approved"
        assert manifest["payload"]["asset_id"] == env.ids.asset_id
        assert manifest["payload"]["requirement_revision"] == 1
        assert manifest["payload"]["filename_hint"] == "product-main.png"
        assert set(manifest["payload"]) == {
            "asset_id",
            "requirement_revision",
            "filename_hint",
            "caption",
        }
        manifest_approval = next(
            item for item in prepared["approvals"] if item["approval_type"] == "delivery_manifest"
        )
        assert manifest_approval["approved_by"] == "output_reviewer"
        assert manifest_approval["artifact_id"] == manifest["id"]
        assert manifest_approval["artifact_digest"] == manifest["digest"]
        assert manifest_approval["rules_version"] == "2.0.0"
        roles = [call.payload["task"]["role"] for call in stub.role_calls_received]
        assert roles == ["delivery_manager", "output_reviewer"]
        assert "generated/" not in prepared_text(prepared)
        assert str(full_settings.resolved_data_dir()) not in prepared_text(prepared)

        still_hidden = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert still_hidden.status_code == 409, still_hidden.text
        assert env.ids.image_bytes not in still_hidden.content

        approved = await _approve(env)
        assert approved["issue"]["code"] == "DELIVERY_APPROVED"
        assert approved["delivery"]["status"] == "approved"
        assert approved["delivery"]["filename"] == "delivery-" + env.ids.asset_id[:8] + "-r1.png"
        assert ".." not in approved["delivery"]["filename"]
        pending_asset = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert pending_asset.status_code == 409, pending_asset.text
        pending_download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert pending_download.status_code == 409, pending_download.text
        assert env.ids.image_bytes not in pending_asset.content

        published = await _publish(env)
        assert published["status"] == "completed"
        assert published["delivery"]["status"] == "published"
        assert published["delivery"]["published_at"]
        assert published["issue"]["code"] == "DELIVERY_PUBLISHED"

        preview = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert preview.status_code == 200, preview.text
        assert preview.content == env.ids.image_bytes
        repeat = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert repeat.status_code == 200
        assert repeat.content == env.ids.image_bytes
        download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert download.status_code == 200, download.text
        assert download.content == env.ids.image_bytes
        assert "attachment" in download.headers["content-disposition"]
        assert published["delivery"]["filename"] in download.headers["content-disposition"]
        assert len(stub.role_calls_received) == 2
        assert json.dumps(published, ensure_ascii=False).find("remote-task-not-public") == -1
        assert str(full_settings.resolved_data_dir()) not in json.dumps(
            published, ensure_ascii=False
        )


def prepared_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize(
    "variant",
    ["wrong_asset", "wrong_revision", "unsafe_filename", "promising_caption"],
)
async def test_manifest_rejects_backend_mismatch_and_unsafe_model_fields(
    full_settings, variant: str
):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        kwargs: dict[str, Any] = {}
        if variant == "wrong_asset":
            kwargs["asset_id"] = str(uuid4())
        elif variant == "wrong_revision":
            kwargs["requirement_revision"] = 99
        elif variant == "unsafe_filename":
            kwargs["filename_hint"] = "../../evil.exe"
        elif variant == "promising_caption":
            kwargs["caption"] = "保证平台必过，绝对保真 https://provider.invalid/private"
        stub.role_responder = _role_responder(ids, **kwargs)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        response = await _post_action(env, "prepare_delivery")
        assert response.status_code == 409, response.text
        assert env.ids.image_bytes not in response.content
        async with env.app.state.session_factory() as session:
            assert await session.scalar(select(Delivery)) is None
        roles = [call.payload["task"]["role"] for call in stub.role_calls_received]
        assert roles == ["delivery_manager"]
        hidden = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert hidden.status_code == 409
        assert env.ids.image_bytes not in hidden.content


@pytest.mark.parametrize(
    "variant",
    ["missing_approval", "incompatible_rules", "wrong_requirement", "wrong_digest", "missing_file"],
)
async def test_invalid_image_evidence_blocks_before_model_call(full_settings, variant: str):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        kwargs: dict[str, Any] = {}
        if variant == "missing_approval":
            kwargs["with_image_approval"] = False
        elif variant == "incompatible_rules":
            kwargs["approval_rules"] = "1.0.0"
        elif variant == "wrong_requirement":
            kwargs["approval_requirement_digest"] = "other-requirement-digest"
        elif variant == "wrong_digest":
            kwargs["asset_digest_override"] = "0" * 64
        elif variant == "missing_file":
            kwargs["write_file"] = False
        ids = await _seed_ready_state(settings, session, **kwargs)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        response = await _post_action(env, "prepare_delivery")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] in {
            "IMAGE_NOT_APPROVED",
            "ASSET_UNAVAILABLE",
            "DIGEST_MISMATCH",
        }
        assert stub.role_calls_received == []
        async with env.app.state.session_factory() as session:
            assert await session.scalar(select(Delivery)) is None


async def test_tampered_file_rule_change_and_revocation_fail_closed(full_settings):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        await _prepare(env)
        await _approve(env)
        await _publish(env)

        published_dir = full_settings.resolved_data_dir() / "published"
        files = list(published_dir.glob("*"))
        assert len(files) == 1
        original = files[0].read_bytes()
        files[0].write_bytes(b"not-an-image")
        tampered = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert tampered.status_code == 409, tampered.text
        assert b"not-an-image" not in tampered.content
        assert env.ids.image_bytes not in tampered.content

        files[0].write_bytes(original)
        restored = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert restored.status_code == 200, restored.text

        # 文件被换成另一张合法图片并同步改写摘要，旧批准仍不得为新字节背书。
        replacement = _png_bytes((200, 10, 10))
        files[0].write_bytes(replacement)
        async with env.app.state.session_factory() as session:
            asset = await session.get(Asset, env.ids.asset_id)
            assert asset is not None
            asset.sha256 = hashlib.sha256(replacement).hexdigest()
            await session.commit()
        replaced = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert replaced.status_code == 409, replaced.text
        assert replacement not in replaced.content

        async with env.app.state.session_factory() as session:
            approval = await session.get(Approval, env.ids.approval_id)
            assert approval is not None
            approval.rules_version = "1.0.0"
            await session.commit()
        incompatible = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert incompatible.status_code == 409, incompatible.text
        assert env.ids.image_bytes not in incompatible.content

        async with env.app.state.session_factory() as session:
            approval = await session.get(Approval, env.ids.approval_id)
            assert approval is not None
            approval.rules_version = "2.0.0"
            approval.revoked_at = format_timestamp(utc_now())
            approval.revocation_reason = "test-revoked"
            await session.commit()
        revoked = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert revoked.status_code == 409, revoked.text
        download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert download.status_code == 409, download.text
        assert env.ids.image_bytes not in download.content


async def test_publish_file_failure_leaves_no_downloadable_half_published_asset(
    full_settings, monkeypatch
):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    real_write_asset_file = s10.write_asset_file

    def fail_write(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated disk failure")

    async with _delivery_env(full_settings, stub, seed) as env:
        await _prepare(env)
        await _approve(env)
        monkeypatch.setattr(s10, "write_asset_file", fail_write)
        failed = await _post_action(env, "publish_delivery")
        assert failed.status_code == 409, failed.text
        assert env.ids.image_bytes not in failed.content

        async with env.app.state.session_factory() as session:
            delivery = await session.scalar(select(Delivery))
            assert delivery is not None
            assert delivery.status == "approved"
            job = await session.get(GenerationJob, env.ids.job_id)
            assert job is not None
            assert job.status == "approved"
        hidden = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert hidden.status_code == 409
        assert env.ids.image_bytes not in hidden.content
        assert list((full_settings.resolved_data_dir() / "published").glob("*")) == []

        monkeypatch.setattr(s10, "write_asset_file", real_write_asset_file)
        published = await _publish(env)
        assert published["delivery"]["status"] == "published"
        preview = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert preview.status_code == 200
        assert preview.content == env.ids.image_bytes


async def test_history_uses_its_own_confirmed_snapshot_after_project_changes(full_settings):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        await _prepare(env)
        await _approve(env)
        await _publish(env)

        async with env.app.state.session_factory() as session:
            project = await session.get(Project, env.ids.project_id)
            assert project is not None
            project.revision = 99
            project.prompt_revision = 99
            project.confirmed_revision = 99
            project.product_description = "后来修改的新需求"
            await session.commit()

        preview = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert preview.status_code == 200, preview.text
        assert preview.content == env.ids.image_bytes
        download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert download.status_code == 200, download.text


async def test_requirement_change_without_immutable_snapshot_blocks_before_model_call(
    full_settings,
):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        async with env.app.state.session_factory() as session:
            project = await session.get(Project, env.ids.project_id)
            assert project is not None
            project.product_description = "后来修改但未重新确认的需求"
            await session.commit()

        response = await _post_action(env, "prepare_delivery")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REQUIREMENT_CHANGED"
        assert stub.role_calls_received == []
        hidden = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert hidden.status_code == 409
        assert env.ids.image_bytes not in hidden.content


async def test_delivery_uses_the_immutable_approved_snapshot_after_project_changes(
    full_settings,
):
    stub = LLMStub()
    old_snapshot = _brief_snapshot()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session, snapshot_artifact=old_snapshot)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        async with env.app.state.session_factory() as session:
            project = await session.get(Project, env.ids.project_id)
            assert project is not None
            project.product_description = "后来修改的新需求"
            await session.commit()

        prepared = await _prepare(env)
        assert prepared["issue"]["code"] == "DELIVERY_MANIFEST_READY"
        delivery_call = next(
            call
            for call in stub.role_calls_received
            if call.payload["task"]["role"] == "delivery_manager"
        )
        requirement = delivery_call.payload["input_data"]["requirement"]
        assert requirement["brief"]["product_description"] == "米色托特包"
        assert requirement["snapshot_digest"] != ""


async def test_publish_database_failure_leaves_no_downloadable_half_published_asset(
    full_settings, monkeypatch
):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    @asynccontextmanager
    async def failing_transaction(*_args: Any, **_kwargs: Any):
        raise RuntimeError("simulated database failure")
        yield  # pragma: no cover

    async with _delivery_env(full_settings, stub, seed) as env:
        await _prepare(env)
        await _approve(env)
        monkeypatch.setattr(s10, "write_transaction", failing_transaction)
        failed = await _post_action(env, "publish_delivery")
        assert failed.status_code != 200
        assert env.ids.image_bytes not in failed.content

        async with env.app.state.session_factory() as session:
            delivery = await session.scalar(select(Delivery))
            assert delivery is not None
            assert delivery.status == "approved"
            job = await session.get(GenerationJob, env.ids.job_id)
            assert job is not None
            assert job.status == "approved"
        hidden = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert hidden.status_code == 409
        assert env.ids.image_bytes not in hidden.content
        assert list((full_settings.resolved_data_dir() / "published").glob("*")) == []


async def test_revoked_delivery_cannot_be_reapproved_with_old_record(full_settings):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        await _prepare(env)
        await _approve(env)
        await _publish(env)
        async with env.app.state.session_factory() as session:
            delivery = await session.scalar(select(Delivery))
            assert delivery is not None
            delivery.status = "revoked"
            await session.commit()

        response = await _post_action(env, "approve_delivery")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "ASSET_UNAVAILABLE"
        hidden = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert hidden.status_code == 409
        assert env.ids.image_bytes not in hidden.content


async def test_tampered_manifest_payload_fails_closed(full_settings):
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> SeedIds:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        await _prepare(env)
        await _approve(env)
        await _publish(env)
        async with env.app.state.session_factory() as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.run_id == env.ids.run_id,
                    Artifact.kind == "delivery_manifest",
                )
            )
            assert artifact is not None
            # 只篡改清单内容、不更新摘要，模拟批准后的内容不再一致。
            artifact.payload_json = json.dumps(
                {
                    "asset_id": env.ids.asset_id,
                    "requirement_revision": 1,
                    "filename_hint": "tampered.png",
                    "caption": "伪造的交付说明",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            await session.commit()
        response = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert response.status_code == 409, response.text
        assert env.ids.image_bytes not in response.content
        download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert download.status_code == 409, download.text
        assert env.ids.image_bytes not in download.content
