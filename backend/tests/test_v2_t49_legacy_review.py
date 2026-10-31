from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Any

import pytest

from app.workflow import templates
from app.workflow.stages import s11
from tests.support.adapters import LLMStub, build_test_registry
from tests.support.apps import run_client
from tests.support.auth import bind_projects_to_test_admin
from tests.test_v2_t01_legacy import (
    NOW,
    OLD_ASSISTANT_TEXT,
    OLD_BRIEF,
    OLD_PROMPT,
    PROVIDER_SECRET,
    _connect,
    _png_bytes,
    _seed_v1,
    _upgrade,
)


class Script:
    """Controlled external model script; no DB/route/approval mocking."""

    def __init__(
        self,
        *,
        decisions: dict[str, str] | None = None,
        quality_decision: str = "pass",
        review_images: bool = True,
    ) -> None:
        self.decisions = decisions or {}
        self.quality_decision = quality_decision
        self.review_images = review_images
        self.calls: list[Any] = []

    def __call__(self, request: Any) -> dict[str, Any]:
        self.calls.append(request)
        task = request.payload["task"]
        role = task["role"]
        if role == "quality_compliance":
            return self._quality(request)
        if role == "delivery_manager":
            return self._delivery(request)
        if role == "output_reviewer":
            return self._review(request)
        raise AssertionError(f"unexpected role {role!r}")

    def _quality(self, request: Any) -> dict[str, Any]:
        data = request.payload["input_data"]
        rules = list(request.payload["task"]["required_rule_ids"])
        evidence = list(request.payload["task"]["allowed_evidence_ids"])[:1]
        checks = [
            {"rule_id": rule_id, "outcome": "pass", "evidence_ids": evidence, "note": ""}
            for rule_id in rules
        ]
        return {
            "role": "quality_compliance",
            "status": "ready",
            "data": {
                "asset_id": data["asset_id"],
                "decision": self.quality_decision,
                "checks": checks,
                "soft_feedback": [],
                "limitations": [],
                "user_summary": "历史图片已完成受控检查。",
            },
            "issues": [],
            "questions": [],
        }

    def _delivery(self, request: Any) -> dict[str, Any]:
        data = request.payload["input_data"]
        return {
            "role": "delivery_manager",
            "status": "ready",
            "data": {
                "asset_id": data["asset_id"],
                "requirement_revision": data["requirement_revision"],
                "filename_hint": "legacy-result.png",
                "caption": "历史图片补审通过，仍保留原检查限制。",
            },
            "issues": [],
            "questions": [],
        }

    def _review(self, request: Any) -> dict[str, Any]:
        target = request.payload["review_targets"][0]
        target_kind = target["target_kind"]
        target_id = target["target_id"]
        rules = list(request.payload["task"]["required_rule_ids"])
        evidence = list(request.payload["task"]["allowed_evidence_ids"])[:1] or [target_id]
        decision = self.decisions.get(target_kind, "pass")
        checks = [
            {
                "rule_id": rule_id,
                "outcome": "pass" if decision == "pass" else "unknown",
                "evidence_ids": evidence,
                "note": "",
            }
            for rule_id in rules
        ]
        return {
            "role": "output_reviewer",
            "status": "ready",
            "data": {"results": [{"target_id": target_id, "decision": decision, "checks": checks}]},
            "issues": [],
            "questions": [],
        }


@asynccontextmanager
async def _environment(settings, full_settings, script: Script):
    _upgrade(settings, "0001")
    ids = _seed_v1(settings)
    _upgrade(settings, "head")
    await bind_projects_to_test_admin(settings, [ids["project_id"]])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)
    async with run_client(full_settings, registry) as client:
        yield client, stub, registry, ids


def _legacy(view: dict[str, Any], kind: str, ref_id: str | None = None) -> dict[str, Any]:
    for item in view["legacy"]:
        if item["kind"] == kind and (ref_id is None or item["ref_id"] == ref_id):
            return item
    raise AssertionError(f"legacy item {kind}/{ref_id} not found: {view['legacy']}")


async def _act(client, project_id: str, item_id: str, expected_revision: int = 1):
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/review_legacy_item",
        json={"expected_revision": expected_revision, "payload": {"item_id": item_id}},
    )


async def _detail(client, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _workflow(client, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _approval_rows(settings) -> list[sqlite3.Row]:
    connection = _connect(settings)
    try:
        return list(connection.execute("SELECT * FROM approvals ORDER BY created_at").fetchall())
    finally:
        connection.close()


def _legacy_rows(settings) -> list[sqlite3.Row]:
    connection = _connect(settings)
    try:
        return list(
            connection.execute("SELECT * FROM legacy_items ORDER BY kind, ref_id").fetchall()
        )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_unselected_legacy_outputs_remain_isolated(settings, full_settings):
    """AC-39：未主动选择时旧文字、提示词和图片字节均不可见。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, stub, _, ids):
        detail = await _detail(client, ids["project_id"])
        assert detail["project"]["legacy_unreviewed"] is True
        assert detail["project"]["brief"]["product_description"] == ""
        assert detail["project"]["brief"]["prompt"] == ""
        assistant = next(
            message
            for message in detail["messages"]
            if message["id"] == ids["assistant_message_id"]
        )
        generation = detail["generations"][0]
        assert assistant["content"] == templates.render("LEGACY_UNREVIEWED")
        assert assistant["brief_snapshot"] is None
        assert generation["prompt"] == ""
        assert generation["brief_snapshot"] is None
        assert generation["legacy_unreviewed"] is True
        text = json.dumps(detail, ensure_ascii=False)
        for secret in (OLD_ASSISTANT_TEXT, OLD_PROMPT, PROVIDER_SECRET, ids["generated_id"]):
            assert secret not in text

        workflow = await _workflow(client, ids["project_id"])
        assert [item["kind"] for item in workflow["legacy"]].count("message") == 1
        assert all(item["status"] == "legacy_unreviewed" for item in workflow["legacy"])
        assert stub.role_calls_received == []

        asset = await client.get(f"/api/assets/{ids['generated_id']}")
        assert asset.status_code == 409
        assert asset.json()["error"]["code"] == "LEGACY_UNREVIEWED"
        download = await client.get(f"/api/assets/{ids['generated_id']}?download=true")
        assert download.status_code == 409


@pytest.mark.asyncio
async def test_review_message_restores_only_that_message(settings, full_settings):
    """AC-40：文字历史按原快照和独立 TEXT 审查恢复，图片仍隔离。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, stub, _, ids):
        item = _legacy(
            await _workflow(client, ids["project_id"]), "message", ids["assistant_message_id"]
        )
        response = await _act(client, ids["project_id"], item["id"])
        assert response.status_code == 200, response.text
        view = response.json()
        assert _legacy(view, "message", ids["assistant_message_id"])["status"] == "approved"
        assert len(stub.role_calls_received) == 1
        call = stub.role_calls_received[0]
        assert call.payload["task"]["role"] == "output_reviewer"
        assert call.payload["review_targets"][0]["target_kind"] == "legacy_message"
        assert call.images == ()
        assert call.payload["review_targets"][0]["content"] == OLD_ASSISTANT_TEXT

        detail = await _detail(client, ids["project_id"])
        message = next(
            message
            for message in detail["messages"]
            if message["id"] == ids["assistant_message_id"]
        )
        assert message["content"] == OLD_ASSISTANT_TEXT
        assert message["brief_snapshot"]["product_description"] == OLD_BRIEF["product_description"]
        generation = detail["generations"][0]
        assert generation["prompt"] == ""
        assert generation["legacy_unreviewed"] is True
        assert (await client.get(f"/api/assets/{ids['generated_id']}")).status_code == 409

        approvals = _approval_rows(settings)
        assert len(approvals) == 1
        approval = approvals[0]
        assert approval["approval_type"] == "legacy_text"
        assert approval["approved_by"] == "output_reviewer"
        assert approval["artifact_digest"] == sha256(OLD_ASSISTANT_TEXT.encode()).hexdigest()
        checks = json.loads(approval["checks_json"])
        assert checks["legacy_item_id"] == item["id"]
        assert checks["content_digest"] == approval["artifact_digest"]
        assert checks["snapshot_digest"]
        assert checks["rule_ids"] == list(s11.LEGACY_TEXT_RULES)
        assert approval["model_fingerprint"].startswith("stub-llm:output_reviewer:")
        assert approval["requirement_snapshot_digest"]
        assert approval["referenced_material_digest"]


@pytest.mark.asyncio
async def test_review_prompt_does_not_restore_image(settings, full_settings):
    """AC-40：提示词按独立文字审查恢复，不能借图片或消息批准。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, stub, _, ids):
        item = _legacy(
            await _workflow(client, ids["project_id"]), "generation", ids["generation_id"]
        )
        response = await _act(client, ids["project_id"], item["id"])
        assert response.status_code == 200, response.text
        assert len(stub.role_calls_received) == 1
        assert (
            stub.role_calls_received[0].payload["review_targets"][0]["target_kind"]
            == "legacy_prompt"
        )

        detail = await _detail(client, ids["project_id"])
        generation = detail["generations"][0]
        assert generation["prompt"] == OLD_PROMPT
        assert (
            generation["brief_snapshot"]["product_description"] == OLD_BRIEF["product_description"]
        )
        assert generation["result_asset_id"] is None
        assert generation["result_asset"] is None
        assert (await client.get(f"/api/assets/{ids['generated_id']}")).status_code == 409
        assistant = next(
            message
            for message in detail["messages"]
            if message["id"] == ids["assistant_message_id"]
        )
        assert assistant["content"] == templates.render("LEGACY_UNREVIEWED")


@pytest.mark.asyncio
async def test_review_image_runs_vision_quality_and_publishes_atomically(settings, full_settings):
    """AC-40：图片经 G06/G07 真实看图审查后才恢复预览和下载。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, stub, registry, ids):
        item = _legacy(await _workflow(client, ids["project_id"]), "asset", ids["generated_id"])
        response = await _act(client, ids["project_id"], item["id"])
        assert response.status_code == 200, response.text
        calls = stub.role_calls_received
        assert [call.payload["task"]["role"] for call in calls] == [
            "quality_compliance",
            "output_reviewer",
            "output_reviewer",
            "delivery_manager",
            "output_reviewer",
        ]
        vq = calls[0]
        assert vq.images and vq.images[0].data == ids["generated_bytes"]
        assert calls[1].images and calls[1].images[0].data == ids["generated_bytes"]
        assert calls[2].images and calls[2].images[0].data == ids["generated_bytes"]
        assert registry.stub_visual.submit_calls == []

        detail = await _detail(client, ids["project_id"])
        generation = detail["generations"][0]
        assert generation["prompt"] == ""
        assert generation["legacy_unreviewed"] is True
        asset = await client.get(f"/api/assets/{ids['generated_id']}")
        assert asset.status_code == 200, asset.text
        assert asset.content == ids["generated_bytes"]
        download = await client.get(f"/api/assets/{ids['generated_id']}?download=true")
        assert download.status_code == 200
        assert download.content == ids["generated_bytes"]
        assert "attachment" in download.headers["content-disposition"]

        workflow = await _workflow(client, ids["project_id"])
        assert _legacy(workflow, "asset", ids["generated_id"])["status"] == "approved"
        assert workflow["delivery"] is not None
        assert workflow["delivery"]["status"] == "published"
        approvals = _approval_rows(settings)
        assert {row["approval_type"] for row in approvals} >= {"image", "delivery_manifest"}
        image_approval = next(row for row in approvals if row["approval_type"] == "image")
        checks = json.loads(image_approval["checks_json"])
        assert checks["asset_id"] == ids["generated_id"]
        assert checks["asset_digest"] == sha256(ids["generated_bytes"]).hexdigest()
        assert checks["legacy_item_id"] == item["id"]
        assert image_approval["requirement_snapshot_digest"]
        assert image_approval["model_fingerprint"].startswith("stub-llm:output_reviewer:")

        connection = _connect(settings)
        try:
            jobs = connection.execute("SELECT * FROM generation_jobs").fetchall()
            generations = connection.execute("SELECT COUNT(*) FROM generations").fetchone()[0]
        finally:
            connection.close()
        assert len(jobs) == 1
        assert jobs[0]["submit_attempted"] == 0
        assert jobs[0]["result_asset_id"] == ids["generated_id"]
        assert generations == 1


@pytest.mark.asyncio
async def test_unknown_review_keeps_all_legacy_outputs_isolated(settings, full_settings):
    """AC-40：unknown 不签发通过、不恢复正文或字节。"""
    script = Script(decisions={"legacy_message": "unknown"})
    async with _environment(settings, full_settings, script) as (client, stub, _, ids):
        item = _legacy(
            await _workflow(client, ids["project_id"]), "message", ids["assistant_message_id"]
        )
        response = await _act(client, ids["project_id"], item["id"])
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_UNKNOWN"
        assert (
            _legacy(view, "message", ids["assistant_message_id"])["status"] == "legacy_unreviewed"
        )
        detail = await _detail(client, ids["project_id"])
        assistant = next(
            message
            for message in detail["messages"]
            if message["id"] == ids["assistant_message_id"]
        )
        assert assistant["content"] == templates.render("LEGACY_UNREVIEWED")
        assert _approval_rows(settings) == []
        assert len(stub.role_calls_received) == 1


@pytest.mark.asyncio
async def test_missing_snapshot_and_reference_block_without_model(settings, full_settings):
    """AC-40：原快照或必要参考图缺失时继续隔离，不猜测、不调用模型。"""
    script = Script()
    _upgrade(settings, "0001")
    ids = _seed_v1(settings)
    connection = _connect(settings)
    try:
        connection.execute(
            "UPDATE messages SET brief_snapshot_json = NULL WHERE id = ?",
            (ids["assistant_message_id"],),
        )
        connection.execute(
            "UPDATE generations SET reference_asset_id = ? WHERE id = ?",
            (str(uuid.uuid4()), ids["generation_id"]),
        )
        connection.commit()
    finally:
        connection.close()
    _upgrade(settings, "head")
    await bind_projects_to_test_admin(settings, [ids["project_id"]])
    stub = LLMStub(role_responder=script)
    registry = build_test_registry(llm=stub)
    async with run_client(full_settings, registry) as client:
        workflow = await _workflow(client, ids["project_id"])
        message_item = _legacy(workflow, "message", ids["assistant_message_id"])
        response = await _act(client, ids["project_id"], message_item["id"])
        assert response.status_code == 200, response.text
        assert response.json()["issue"]["code"] == "LEGACY_SNAPSHOT_MISSING"
        assert stub.role_calls_received == []

        asset_item = _legacy(
            await _workflow(client, ids["project_id"]), "asset", ids["generated_id"]
        )
        response = await _act(client, ids["project_id"], asset_item["id"])
        assert response.status_code == 200, response.text
        assert response.json()["issue"]["code"] == "LEGACY_REFERENCE_MISSING"
        assert stub.role_calls_received == []
        assert (await client.get(f"/api/assets/{ids['generated_id']}")).status_code == 409


@pytest.mark.asyncio
async def test_repeated_review_is_idempotent_and_restart_reuses_approval(settings, full_settings):
    """AC-40：重复补审不重复调用模型；重开同一数据仍可核验。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, stub, _, ids):
        item = _legacy(
            await _workflow(client, ids["project_id"]), "message", ids["assistant_message_id"]
        )
        first = await _act(client, ids["project_id"], item["id"])
        assert first.status_code == 200, first.text
        second = await _act(client, ids["project_id"], item["id"])
        assert second.status_code == 200, second.text
        assert len(stub.role_calls_received) == 1
        assert len(_approval_rows(settings)) == 1

    # Reopen the same SQLite/files with a fresh adapter; viewing is model-free.
    stub2 = LLMStub(role_responder=Script())
    registry2 = build_test_registry(llm=stub2)
    async with run_client(full_settings, registry2) as client:
        detail = await _detail(client, ids["project_id"])
        message = next(
            message
            for message in detail["messages"]
            if message["id"] == ids["assistant_message_id"]
        )
        assert message["content"] == OLD_ASSISTANT_TEXT
        assert stub2.role_calls_received == []


@pytest.mark.asyncio
async def test_tampered_or_revoked_image_access_fails_closed(settings, full_settings):
    """AC-32：文件替换或批准撤销后 G08 重新阻断。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, _, _, ids):
        item = _legacy(await _workflow(client, ids["project_id"]), "asset", ids["generated_id"])
        response = await _act(client, ids["project_id"], item["id"])
        assert response.status_code == 200, response.text
        assert (await client.get(f"/api/assets/{ids['generated_id']}")).status_code == 200

        (settings.data_dir / "generated" / f"{ids['generated_id']}.png").write_bytes(
            _png_bytes((1, 2, 3))
        )
        tampered = await client.get(f"/api/assets/{ids['generated_id']}")
        assert tampered.status_code == 409
        assert tampered.json()["error"]["code"] == "DIGEST_MISMATCH"

        # Restore the original bytes and revoke the image approval.
        (settings.data_dir / "generated" / f"{ids['generated_id']}.png").write_bytes(
            ids["generated_bytes"]
        )
        connection = _connect(settings)
        try:
            connection.execute(
                "UPDATE approvals SET revoked_at = ? WHERE approval_type = 'image'",
                (NOW,),
            )
            connection.commit()
        finally:
            connection.close()
        revoked = await client.get(f"/api/assets/{ids['generated_id']}")
        assert revoked.status_code == 409
        assert revoked.json()["error"]["code"] == "APPROVAL_MISSING"


@pytest.mark.asyncio
async def test_concurrent_duplicate_review_calls_model_once(settings, full_settings):
    """AC-40：并发重复同一补审不会重复执行或批量批准其他历史项。"""
    script = Script()
    async with _environment(settings, full_settings, script) as (client, stub, _, ids):
        item = _legacy(
            await _workflow(client, ids["project_id"]), "message", ids["assistant_message_id"]
        )
        responses = await asyncio.gather(
            _act(client, ids["project_id"], item["id"]),
            _act(client, ids["project_id"], item["id"]),
        )
        assert all(response.status_code in {200, 409} for response in responses)
        assert len(stub.role_calls_received) == 1
        assert len(_approval_rows(settings)) == 1
        rows = {row["ref_id"]: row["status"] for row in _legacy_rows(settings)}
        assert rows[ids["assistant_message_id"]] == "approved"
        assert rows[ids["generation_id"]] == "legacy_unreviewed"
        assert rows[ids["generated_id"]] == "legacy_unreviewed"
