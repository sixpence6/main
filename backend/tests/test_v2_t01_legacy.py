from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from PIL import Image

from app.workflow import templates
from tests.support.adapters import build_test_registry
from tests.support.apps import run_client

BACKEND_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
NOW = "2026-01-01T00:00:00.000000Z"
OLD_ASSISTANT_TEXT = "旧模型正文：这是未经审查的历史文案"
OLD_PROMPT = "旧历史提示词：生成一张未经审查的商品图"
OLD_BRIEF = {
    "product_description": "旧模型商品描述",
    "purpose": "旧模型用途",
    "style": "旧模型风格",
    "composition": "旧模型构图",
    "text_content": "旧模型画面文字",
    "image_size": "1024x1024",
    "reference_asset_id": None,
    "prompt": OLD_PROMPT,
}
PROVIDER_SECRET = "https://provider.invalid/private/old-result.png"


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _alembic_config(settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    return config


def _upgrade(settings, revision: str) -> None:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(settings), revision)


def _connect(settings):
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    return connection


@dataclass
class LegacyEnv:
    client: object
    registry: object
    settings: object
    ids: dict[str, str]


def _insert_project(
    connection: sqlite3.Connection, project_id: str, *, title: str = "历史项目"
) -> None:
    connection.execute(
        """
        INSERT INTO projects (
            id, title, revision, prompt_revision, confirmed_revision, brief_status,
            product_description, purpose, style, composition, text_content, image_size,
            prompt, reference_asset_id, organize_operation_id, brief_error_code,
            brief_error_message, created_at, updated_at
        ) VALUES (?, ?, 1, 1, 1, 'confirmed', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
        """,
        (
            project_id,
            title,
            OLD_BRIEF["product_description"],
            OLD_BRIEF["purpose"],
            OLD_BRIEF["style"],
            OLD_BRIEF["composition"],
            OLD_BRIEF["text_content"],
            OLD_BRIEF["image_size"],
            OLD_PROMPT,
            NOW,
            NOW,
        ),
    )


def _insert_asset(
    connection: sqlite3.Connection,
    *,
    asset_id: str,
    project_id: str,
    kind: str,
    relative_path: str,
    byte_size: int,
    review_status: str | None = None,
    sha256_value: str | None = None,
) -> None:
    columns = [
        "id",
        "project_id",
        "kind",
        "relative_path",
        "mime_type",
        "width",
        "height",
        "byte_size",
        "created_at",
    ]
    values = [asset_id, project_id, kind, relative_path, "image/png", 8, 8, byte_size, NOW]
    if review_status is not None:
        columns.extend(["review_status", "sha256"])
        values.extend([review_status, sha256_value])
    connection.execute(
        f"INSERT INTO assets ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        values,
    )


def _seed_v1(settings) -> dict[str, str]:
    """在迁移到 0001 的数据库里构造真实 v1 数据，再升级到 head。"""
    project_id = str(uuid.uuid4())
    reference_id = str(uuid.uuid4())
    generated_id = str(uuid.uuid4())
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    generation_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    data_dir = settings.data_dir
    (data_dir / "references").mkdir(parents=True, exist_ok=True)
    (data_dir / "generated").mkdir(parents=True, exist_ok=True)
    reference_bytes = _png_bytes((20, 120, 220))
    generated_bytes = _png_bytes((220, 20, 20))
    (data_dir / "references" / f"{reference_id}.png").write_bytes(reference_bytes)
    (data_dir / "generated" / f"{generated_id}.png").write_bytes(generated_bytes)
    connection = _connect(settings)
    try:
        _insert_project(connection, project_id)
        _insert_asset(
            connection,
            asset_id=reference_id,
            project_id=project_id,
            kind="reference",
            relative_path=f"references/{reference_id}.png",
            byte_size=len(reference_bytes),
        )
        _insert_asset(
            connection,
            asset_id=generated_id,
            project_id=project_id,
            kind="generated",
            relative_path=f"generated/{generated_id}.png",
            byte_size=len(generated_bytes),
        )
        connection.execute(
            "UPDATE projects SET reference_asset_id = ? WHERE id = ?", (reference_id, project_id)
        )
        connection.execute(
            """
            INSERT INTO messages (
                id, project_id, sequence_no, role, content, project_revision,
                brief_snapshot_json, operation_id, created_at
            ) VALUES (?, ?, 1, 'user', ?, 1, NULL, NULL, ?)
            """,
            (
                user_message_id,
                project_id,
                "用户原文：<script>alert('x')</script> 请参考上传图",
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO messages (
                id, project_id, sequence_no, role, content, project_revision,
                brief_snapshot_json, operation_id, created_at
            ) VALUES (?, ?, 2, 'assistant', ?, 1, ?, NULL, ?)
            """,
            (
                assistant_message_id,
                project_id,
                OLD_ASSISTANT_TEXT,
                json.dumps(OLD_BRIEF, ensure_ascii=False),
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO generations (
                id, project_id, version_no, request_id, request_canonical, request_digest,
                confirmed_revision, status, stage, brief_snapshot_json, prompt, image_size,
                reference_asset_id, adapter_id, model, service_fingerprint, remote_task_id,
                result_locator_json, result_asset_id, error_code, error_message,
                submit_attempted, created_at, started_at, completed_at
            ) VALUES (?, ?, 1, ?, ?, ?, 1, 'succeeded', 'done', ?, ?, '1024x1024', ?, 'stub-visual',
                      'stub-model', 'fp', 'remote-secret-task', ?, ?, NULL, NULL, 1, ?, ?, ?)
            """,
            (
                generation_id,
                project_id,
                request_id,
                "canonical-v1-very-old",
                "digest-v1-very-old",
                json.dumps(OLD_BRIEF, ensure_ascii=False),
                OLD_PROMPT,
                reference_id,
                json.dumps({"remote_url": PROVIDER_SECRET}, ensure_ascii=False),
                generated_id,
                NOW,
                NOW,
                NOW,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "project_id": project_id,
        "reference_id": reference_id,
        "generated_id": generated_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "generation_id": generation_id,
        "request_id": request_id,
        "reference_bytes": reference_bytes,
        "generated_bytes": generated_bytes,
    }


@pytest_asyncio.fixture
async def legacy_env(settings, full_settings):
    _upgrade(settings, "0001")
    ids = _seed_v1(settings)
    _upgrade(settings, "head")
    registry = build_test_registry()
    async with run_client(full_settings, registry) as client:
        yield LegacyEnv(client=client, registry=registry, settings=settings, ids=ids)


def _seed_missing_approval_assets(settings) -> dict[str, str]:
    """head 之后构造缺审批/缺交付的派生资产，验证失败关闭。"""
    project_id = str(uuid.uuid4())
    no_approval_id = str(uuid.uuid4())
    approved_id = str(uuid.uuid4())
    missing_file_id = str(uuid.uuid4())
    data_dir = settings.data_dir
    (data_dir / "generated").mkdir(parents=True, exist_ok=True)
    content = _png_bytes((10, 200, 10))
    for asset_id in (no_approval_id, approved_id):
        (data_dir / "generated" / f"{asset_id}.png").write_bytes(content)
    digest = sha256(content).hexdigest()
    connection = _connect(settings)
    try:
        _insert_project(connection, project_id, title="派生项目")
        _insert_asset(
            connection,
            asset_id=no_approval_id,
            project_id=project_id,
            kind="generated",
            relative_path=f"generated/{no_approval_id}.png",
            byte_size=len(content),
            review_status="approved",
            sha256_value=digest,
        )
        _insert_asset(
            connection,
            asset_id=approved_id,
            project_id=project_id,
            kind="generated",
            relative_path=f"generated/{approved_id}.png",
            byte_size=len(content),
            review_status="approved",
            sha256_value=digest,
        )
        _insert_asset(
            connection,
            asset_id=missing_file_id,
            project_id=project_id,
            kind="generated",
            relative_path=f"generated/{missing_file_id}.png",
            byte_size=len(content),
            review_status="approved",
            sha256_value=digest,
        )
        connection.execute(
            """
            INSERT INTO approvals (
                id, project_id, artifact_id, approval_type, artifact_digest, approved_by,
                requirement_snapshot_digest, referenced_material_digest, prompt_version,
                rules_version, model_fingerprint, checks_json, created_at, revoked_at,
                revocation_reason
            ) VALUES (
                ?, ?, NULL, 'image', ?, 'user', 'req', 'ref', 'p1', 'r1', 'fp', ?, ?, NULL, NULL
            )
            """,
            (
                str(uuid.uuid4()),
                project_id,
                digest,
                json.dumps({"asset_id": approved_id, "asset_digest": digest}),
                NOW,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return {
        "project_id": project_id,
        "no_approval_id": no_approval_id,
        "approved_id": approved_id,
        "missing_file_id": missing_file_id,
    }


def _insert_image_approval(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    approval_type: str = "image",
    checks: dict[str, object],
    artifact_digest: str = "approval-artifact-digest",
    artifact_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO approvals (
            id, project_id, artifact_id, approval_type, artifact_digest, approved_by,
            requirement_snapshot_digest, referenced_material_digest, prompt_version,
            rules_version, model_fingerprint, checks_json, created_at, revoked_at,
            revocation_reason
        ) VALUES (?, ?, ?, ?, ?, 'user', 'req', 'ref', 'p1', 'r1', 'fp', ?, ?, NULL, NULL)
        """,
        (
            str(uuid.uuid4()),
            project_id,
            artifact_id,
            approval_type,
            artifact_digest,
            json.dumps(checks, ensure_ascii=False),
            NOW,
        ),
    )


def _seed_missing_metadata_assets(settings) -> dict[str, str]:
    """构造审批字段或交付字段不完整的派生资产。"""
    data_dir = settings.data_dir
    (data_dir / "generated").mkdir(parents=True, exist_ok=True)
    content = _png_bytes((30, 30, 200))
    digest = sha256(content).hexdigest()
    ids: dict[str, str] = {}
    connection = _connect(settings)
    try:
        scenarios = {
            "no_checks": {},
            "only_asset_id": {"asset_id": None},
            "empty_delivery_digest": {"asset_id": None, "asset_digest": digest},
            "empty_delivery_filename": {"asset_id": None, "asset_digest": digest},
        }
        for name, checks in scenarios.items():
            project_id = str(uuid.uuid4())
            asset_id = str(uuid.uuid4())
            ids[f"{name}_project"] = project_id
            ids[f"{name}_asset"] = asset_id
            (data_dir / "generated" / f"{asset_id}.png").write_bytes(content)
            _insert_project(connection, project_id, title=f"派生项目-{name}")
            _insert_asset(
                connection,
                asset_id=asset_id,
                project_id=project_id,
                kind="generated",
                relative_path=f"generated/{asset_id}.png",
                byte_size=len(content),
                review_status="approved",
                sha256_value=digest,
            )
            actual_checks = dict(checks)
            if "asset_id" in actual_checks and actual_checks["asset_id"] is None:
                actual_checks["asset_id"] = asset_id
            _insert_image_approval(connection, project_id=project_id, checks=actual_checks)
            if name.startswith("empty_delivery_"):
                connection.execute(
                    """
                    INSERT INTO deliveries (
                        id, project_id, generation_job_id, asset_id, manifest_artifact_id,
                        status, filename, caption, asset_digest, approved_at, published_at,
                        created_at
                    ) VALUES (?, ?, ?, ?, NULL, 'published', ?, '', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        project_id,
                        str(uuid.uuid4()),
                        asset_id,
                        "" if name == "empty_delivery_filename" else "result.png",
                        "" if name == "empty_delivery_digest" else digest,
                        NOW,
                        NOW,
                        NOW,
                    ),
                )
        connection.commit()
    finally:
        connection.close()
    return ids


def _seed_artifact_binding_assets(settings) -> dict[str, str]:
    """构造带 artifact 审批的资产，验证 artifact 绑定字段缺一不可。"""
    data_dir = settings.data_dir
    (data_dir / "generated").mkdir(parents=True, exist_ok=True)
    content = _png_bytes((40, 80, 120))
    digest = sha256(content).hexdigest()
    ids: dict[str, str] = {}
    connection = _connect(settings)
    try:
        scenarios = {
            # 合法的 artifact 兜底审批，必须仍可读取。
            "artifact_valid": ("approved", True),
            # checks 缺少 asset_digest，即使 artifact 完整也必须拒绝。
            "missing_checks_digest": ("approved", True),
            # artifact 载荷缺少 asset_digest，必须拒绝。
            "missing_payload_digest": ("approved", False),
            # artifact 未批准，必须拒绝。
            "unapproved_artifact": ("candidate", True),
        }
        for name, (artifact_status, payload_has_digest) in scenarios.items():
            project_id = str(uuid.uuid4())
            asset_id = str(uuid.uuid4())
            run_id = str(uuid.uuid4())
            artifact_id = str(uuid.uuid4())
            artifact_digest = sha256(f"{name}:artifact".encode()).hexdigest()
            ids[f"{name}_project"] = project_id
            ids[f"{name}_asset"] = asset_id
            (data_dir / "generated" / f"{asset_id}.png").write_bytes(content)
            _insert_project(connection, project_id, title=f"派生项目-{name}")
            _insert_asset(
                connection,
                asset_id=asset_id,
                project_id=project_id,
                kind="generated",
                relative_path=f"generated/{asset_id}.png",
                byte_size=len(content),
                review_status="approved",
                sha256_value=digest,
            )
            connection.execute(
                """
                INSERT INTO workflow_runs (
                    id, project_id, sequence_no, stage, status, intent_route, intent_action,
                    requirement_revision, issue_code, issue_message, template_id,
                    template_params_json, created_at, updated_at
                ) VALUES (?, ?, 1, 'S10', 'active', NULL, NULL, 0, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (run_id, project_id, NOW, NOW),
            )
            payload: dict[str, object] = {"asset_id": asset_id}
            if payload_has_digest:
                payload["asset_digest"] = digest
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, run_id, project_id, kind, revision_no, status,
                    payload_json, digest, created_at
                ) VALUES (?, ?, ?, 'result_image', 1, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    run_id,
                    project_id,
                    artifact_status,
                    json.dumps(payload, ensure_ascii=False),
                    artifact_digest,
                    NOW,
                ),
            )
            checks: dict[str, object] = {}
            if name != "missing_checks_digest":
                checks["asset_digest"] = digest
            if name in {"artifact_valid", "missing_checks_digest"}:
                checks["asset_id"] = asset_id
            _insert_image_approval(
                connection,
                project_id=project_id,
                checks=checks,
                artifact_digest=artifact_digest,
                artifact_id=artifact_id,
            )
        connection.commit()
    finally:
        connection.close()
    return ids


async def _message_count(settings) -> int:
    connection = _connect(settings)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        connection.close()


async def _generation_count(settings) -> int:
    connection = _connect(settings)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM generations").fetchone()[0])
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_v1_migration_marks_old_outputs_and_preserves_inputs(legacy_env: LegacyEnv):
    connection = _connect(legacy_env.settings)
    try:
        asset = connection.execute(
            "SELECT review_status FROM assets WHERE id = ?", (legacy_env.ids["generated_id"],)
        ).fetchone()
        reference = connection.execute(
            "SELECT review_status FROM assets WHERE id = ?", (legacy_env.ids["reference_id"],)
        ).fetchone()
        legacy_rows = connection.execute(
            "SELECT kind, ref_id, status FROM legacy_items ORDER BY kind, ref_id"
        ).fetchall()
        user = connection.execute(
            "SELECT content FROM messages WHERE id = ?", (legacy_env.ids["user_message_id"],)
        ).fetchone()
        assistant = connection.execute(
            "SELECT content, brief_snapshot_json FROM messages WHERE id = ?",
            (legacy_env.ids["assistant_message_id"],),
        ).fetchone()
    finally:
        connection.close()

    assert asset["review_status"] == "legacy_unreviewed"
    assert reference["review_status"] != "legacy_unreviewed"
    marked = {(row["kind"], row["ref_id"]): row["status"] for row in legacy_rows}
    assert marked[("asset", legacy_env.ids["generated_id"])] == "legacy_unreviewed"
    assert marked[("generation", legacy_env.ids["generation_id"])] == "legacy_unreviewed"
    assert marked[("message", legacy_env.ids["assistant_message_id"])] == "legacy_unreviewed"
    assert user["content"].startswith("用户原文")
    assert assistant["content"] == OLD_ASSISTANT_TEXT


@pytest.mark.asyncio
async def test_detail_masks_legacy_outputs_and_keeps_safe_input_preview(legacy_env: LegacyEnv):
    response = await legacy_env.client.get(f"/api/projects/{legacy_env.ids['project_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    project = body["project"]
    messages = {message["id"]: message for message in body["messages"]}
    generation = body["generations"][0]
    placeholder = templates.render("LEGACY_UNREVIEWED")

    assert project["title"] == "历史项目"
    assert project.get("legacy_unreviewed") is True
    assert project.get("legacy_notice") == placeholder
    assert project["brief"]["product_description"] == ""
    assert project["brief"]["prompt"] == ""
    assert project["brief_status"] == "draft"
    assert project["prompt_revision"] is None
    assert project["confirmed_revision"] is None
    assert project["reference_asset"]["id"] == legacy_env.ids["reference_id"]
    assert project["reference_asset"]["kind"] == "reference"
    assert project["reference_asset"]["availability"] == "available"
    assert project["reference_asset"]["review_status"] == "reference"

    user_message = messages[legacy_env.ids["user_message_id"]]
    assistant_message = messages[legacy_env.ids["assistant_message_id"]]
    assert "用户原文" in user_message["content"]
    assert user_message.get("legacy_unreviewed") is not True
    assert assistant_message["content"] == placeholder
    assert assistant_message["brief_snapshot"] is None
    assert assistant_message.get("legacy_unreviewed") is True

    assert generation["status"] == "unknown"
    assert generation["stage"] == "done"
    assert generation["prompt"] == ""
    assert generation["brief_snapshot"] is None
    assert generation["remote_task_id"] is None
    assert generation["result_asset_id"] is None
    assert generation["result_asset"] is None
    assert generation["error"] is None
    assert generation["can_retrieve_result"] is False
    assert generation.get("legacy_unreviewed") is True

    text = response.text
    assert OLD_ASSISTANT_TEXT not in text
    assert OLD_PROMPT not in text
    assert PROVIDER_SECRET not in text
    assert legacy_env.ids["generated_id"] not in text


@pytest.mark.asyncio
async def test_missing_approval_and_direct_asset_paths_fail_closed(legacy_env: LegacyEnv):
    ids = _seed_missing_approval_assets(legacy_env.settings)

    legacy = await legacy_env.client.get(f"/api/assets/{legacy_env.ids['generated_id']}")
    assert legacy.status_code == 409
    assert legacy.json()["error"]["code"] == "LEGACY_UNREVIEWED"
    assert legacy_env.ids["generated_id"] not in legacy.text
    assert PROVIDER_SECRET not in legacy.text

    legacy_download = await legacy_env.client.get(
        f"/api/assets/{legacy_env.ids['generated_id']}?download=true"
    )
    assert legacy_download.status_code == 409
    assert legacy_download.json()["error"]["code"] == "LEGACY_UNREVIEWED"

    no_approval = await legacy_env.client.get(f"/api/assets/{ids['no_approval_id']}")
    assert no_approval.status_code == 409
    assert no_approval.json()["error"]["code"] in {"APPROVAL_MISSING", "DIGEST_MISMATCH"}
    assert len(no_approval.content) < 1024

    approved_preview = await legacy_env.client.get(f"/api/assets/{ids['approved_id']}")
    assert approved_preview.status_code == 200
    assert approved_preview.headers["content-type"].startswith("image/png")
    assert approved_preview.content == _png_bytes((10, 200, 10))

    approved_download = await legacy_env.client.get(
        f"/api/assets/{ids['approved_id']}?download=true"
    )
    assert approved_download.status_code == 409
    assert approved_download.json()["error"]["code"] == "NOT_PUBLISHED"

    missing_file = await legacy_env.client.get(f"/api/assets/{ids['missing_file_id']}")
    assert missing_file.status_code in {404, 409}
    assert missing_file.json()["error"]["code"] in {
        "ASSET_FILE_MISSING",
        "APPROVAL_MISSING",
        "DIGEST_MISMATCH",
    }

    reference = await legacy_env.client.get(f"/api/assets/{legacy_env.ids['reference_id']}")
    assert reference.status_code == 200
    assert reference.content == legacy_env.ids["reference_bytes"]
    assert reference.headers["content-type"].startswith("image/png")


@pytest.mark.asyncio
async def test_legacy_write_entrances_are_paused_without_provider_calls(legacy_env: LegacyEnv):
    ids = legacy_env.ids
    before_messages = await _message_count(legacy_env.settings)
    before_generations = await _generation_count(legacy_env.settings)

    message = await legacy_env.client.post(
        f"/api/projects/{ids['project_id']}/messages",
        json={"content": "继续修改历史项目", "expected_revision": 1},
    )
    assert message.status_code == 409
    assert message.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"
    assert message.json()["error"]["message"] == templates.render("LEGACY_FLOW_DISABLED")

    brief = await legacy_env.client.patch(
        f"/api/projects/{ids['project_id']}/brief",
        json={"expected_revision": 1, "product_description": "新描述"},
    )
    assert brief.status_code == 409
    assert brief.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

    confirm = await legacy_env.client.post(
        f"/api/projects/{ids['project_id']}/confirm", json={"revision": 1}
    )
    assert confirm.status_code == 409
    assert confirm.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

    replay = await legacy_env.client.post(
        f"/api/projects/{ids['project_id']}/generations",
        json={
            "request_id": ids["request_id"],
            "confirmed_revision": 1,
            "acknowledge_unknown": None,
        },
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

    retrieve = await legacy_env.client.post(
        f"/api/generations/{ids['generation_id']}/retrieve-result", json={}
    )
    assert retrieve.status_code == 409
    assert retrieve.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

    assert await _message_count(legacy_env.settings) == before_messages
    assert await _generation_count(legacy_env.settings) == before_generations
    assert legacy_env.registry.stub_llm.calls == []
    assert legacy_env.registry.stub_visual.submit_calls == []


@pytest.mark.asyncio
async def test_generation_and_workflow_queries_do_not_leak_old_result(legacy_env: LegacyEnv):
    ids = legacy_env.ids
    generation = await legacy_env.client.get(f"/api/generations/{ids['generation_id']}")
    assert generation.status_code == 200
    body = generation.json()
    assert body["status"] == "unknown"
    assert body["result_asset_id"] is None
    assert body["result_asset"] is None
    assert body["prompt"] == ""
    assert PROVIDER_SECRET not in generation.text

    workflow = await legacy_env.client.get(f"/api/projects/{ids['project_id']}/workflow")
    assert workflow.status_code == 200
    workflow_body = workflow.json()
    legacy = {(item["kind"], item["ref_id"]): item for item in workflow_body["legacy"]}
    assert legacy[("asset", ids["generated_id"])]["status"] == "legacy_unreviewed"
    assert legacy[("message", ids["assistant_message_id"])]["status"] == "legacy_unreviewed"
    assert legacy[("generation", ids["generation_id"])]["status"] == "legacy_unreviewed"
    assert OLD_ASSISTANT_TEXT not in workflow.text
    assert OLD_PROMPT not in workflow.text
    assert PROVIDER_SECRET not in workflow.text


@pytest.mark.asyncio
async def test_project_list_masks_legacy_outputs(legacy_env: LegacyEnv):
    response = await legacy_env.client.get("/api/projects")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    summary = next(item for item in items if item["id"] == legacy_env.ids["project_id"])
    placeholder = templates.render("LEGACY_UNREVIEWED")

    assert summary["title"] == "历史项目"
    assert summary["brief_status"] == "draft"
    assert summary["latest_generation_status"] == "unknown"
    assert summary["legacy_unreviewed"] is True
    assert summary["legacy_notice"] == placeholder
    assert OLD_ASSISTANT_TEXT not in response.text
    assert OLD_PROMPT not in response.text
    assert PROVIDER_SECRET not in response.text


@pytest.mark.asyncio
async def test_missing_approval_metadata_and_delivery_fields_fail_closed(legacy_env: LegacyEnv):
    ids = _seed_missing_metadata_assets(legacy_env.settings)
    for name in ("no_checks", "only_asset_id"):
        response = await legacy_env.client.get(f"/api/assets/{ids[f'{name}_asset']}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "DIGEST_MISMATCH"
        assert len(response.content) < 1024

    binding_ids = _seed_artifact_binding_assets(legacy_env.settings)
    valid = await legacy_env.client.get(f"/api/assets/{binding_ids['artifact_valid_asset']}")
    assert valid.status_code == 200
    assert valid.content == _png_bytes((40, 80, 120))
    for name in ("missing_checks_digest", "missing_payload_digest", "unapproved_artifact"):
        response = await legacy_env.client.get(f"/api/assets/{binding_ids[f'{name}_asset']}")
        assert response.status_code == 409, (name, response.text)
        assert response.json()["error"]["code"] == "DIGEST_MISMATCH"
        assert len(response.content) < 1024

    for name in ("empty_delivery_digest", "empty_delivery_filename"):
        response = await legacy_env.client.get(f"/api/assets/{ids[f'{name}_asset']}?download=true")
        assert response.status_code == 409
        assert response.json()["error"]["code"] in {
            "DIGEST_MISMATCH",
            "NOT_PUBLISHED",
        }
        assert len(response.content) < 1024


@pytest.mark.asyncio
async def test_legacy_reference_writes_are_paused_without_changes(legacy_env: LegacyEnv):
    project_id = legacy_env.ids["project_id"]
    connection = _connect(legacy_env.settings)
    try:
        before_reference = connection.execute(
            "SELECT reference_asset_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()["reference_asset_id"]
        before_assets = int(
            connection.execute(
                "SELECT COUNT(*) FROM assets WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()

    upload = await legacy_env.client.post(
        f"/api/projects/{project_id}/reference",
        data={"expected_revision": "1"},
        files={"file": ("new-reference.png", _png_bytes((1, 2, 3)), "image/png")},
    )
    assert upload.status_code == 409
    assert upload.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

    delete = await legacy_env.client.delete(
        f"/api/projects/{project_id}/reference", params={"expected_revision": 1}
    )
    assert delete.status_code == 409
    assert delete.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"

    connection = _connect(legacy_env.settings)
    try:
        after_reference = connection.execute(
            "SELECT reference_asset_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()["reference_asset_id"]
        after_assets = int(
            connection.execute(
                "SELECT COUNT(*) FROM assets WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert after_reference == before_reference
    assert after_assets == before_assets
    assert legacy_env.registry.stub_llm.calls == []
    assert legacy_env.registry.stub_visual.submit_calls == []


@pytest.mark.asyncio
async def test_repeated_migration_is_idempotent(legacy_env: LegacyEnv):
    connection = _connect(legacy_env.settings)
    try:
        before = {
            "legacy_count": int(
                connection.execute("SELECT COUNT(*) FROM legacy_items").fetchone()[0]
            ),
            "asset_status": connection.execute(
                "SELECT review_status FROM assets WHERE id = ?",
                (legacy_env.ids["generated_id"],),
            ).fetchone()["review_status"],
            "assistant": connection.execute(
                "SELECT content FROM messages WHERE id = ?",
                (legacy_env.ids["assistant_message_id"],),
            ).fetchone()["content"],
        }
    finally:
        connection.close()

    _upgrade(legacy_env.settings, "head")

    connection = _connect(legacy_env.settings)
    try:
        after = {
            "legacy_count": int(
                connection.execute("SELECT COUNT(*) FROM legacy_items").fetchone()[0]
            ),
            "asset_status": connection.execute(
                "SELECT review_status FROM assets WHERE id = ?",
                (legacy_env.ids["generated_id"],),
            ).fetchone()["review_status"],
            "assistant": connection.execute(
                "SELECT content FROM messages WHERE id = ?",
                (legacy_env.ids["assistant_message_id"],),
            ).fetchone()["content"],
        }
    finally:
        connection.close()
    assert after == before
    assert after["asset_status"] == "legacy_unreviewed"
    assert after["assistant"] == OLD_ASSISTANT_TEXT
    assert legacy_env.registry.stub_llm.calls == []
    assert legacy_env.registry.stub_visual.submit_calls == []
