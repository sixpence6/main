from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import select

from app.acceptance import GenerationIntent
from app.auth.models import Account
from app.db import read_session, write_transaction
from app.models import Generation, Message, Project
from app.services.projects import decode_cursor, encode_cursor
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import Approval, Artifact, RevisionBudget, WorkflowRun
from tests.support.auth import TEST_ADMIN_EMAIL
from tests.test_v2_t40_prompt_review import environment, setup_confirmed_project


async def _create_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def _admin_id(app) -> str:
    async with read_session(app.state.session_factory) as session:
        account = await session.scalar(
            select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
        )
        assert account is not None
        return account.id


async def _add_account(app, email: str) -> str:
    now = format_timestamp(utc_now())
    async with write_transaction(app.state.session_factory) as session:
        account = Account(
            id=str(uuid4()),
            email_normalized=email,
            password_hash=None,
            is_admin=0,
            is_disabled=0,
            session_epoch=0,
            created_at=now,
            updated_at=now,
        )
        session.add(account)
        await session.flush()
        return account.id


async def _set_project_rows(app, rows: list[tuple[str, str, str]]) -> None:
    """Rows are (project_id, title, updated_at)."""
    async with write_transaction(app.state.session_factory) as session:
        for project_id, title, updated_at in rows:
            project = await session.get(Project, project_id)
            assert project is not None
            project.title = title
            project.updated_at = updated_at
            await session.flush()


async def _add_generation(
    app,
    *,
    project_id: str,
    owner_id: str,
    version_no: int,
    confirmed_revision: int,
    status: str,
) -> None:
    now = format_timestamp(utc_now())
    async with write_transaction(app.state.session_factory) as session:
        session.add(
            Generation(
                id=str(uuid4()),
                project_id=project_id,
                owner_account_id=owner_id,
                operator_account_id=owner_id,
                authorization_ref=None,
                intent_id=None,
                version_no=version_no,
                request_id=str(uuid4()),
                request_canonical="{}",
                request_digest="d" * 64,
                confirmed_revision=confirmed_revision,
                status=status,
                stage="done" if status in {"succeeded", "failed", "unknown"} else "waiting_remote",
                brief_snapshot_json="{}",
                prompt="prompt",
                image_size="1024x1024",
                reference_asset_id=None,
                adapter_id="stub-visual",
                model="stub-model",
                service_fingerprint="f" * 64,
                submit_attempted=1 if status != "queued" else 0,
                created_at=now,
                started_at=now,
                completed_at=now if status in {"succeeded", "failed", "unknown"} else None,
            )
        )
        await session.flush()


def _titles(items: list[dict]) -> list[str]:
    return [item["title"] for item in items]


async def test_search_finds_project_on_unloaded_page(app, client):
    created = [await _create_project(client) for _ in range(22)]
    rows = []
    for index, project in enumerate(created):
        rows.append(
            (
                project["id"],
                f"Project {index:02d}",
                "2025-01-01T00:00:00.000Z"
                if index == 3
                else f"2026-01-01T00:00:{index:02d}.000Z",
            )
        )
    await _set_project_rows(app, rows)
    target = created[3]
    target_title = "Project 03"

    first = await client.get("/api/projects", params={"limit": 20})
    assert first.status_code == 200, first.text
    assert target["id"] not in [item["id"] for item in first.json()["items"]]
    assert first.json()["next_cursor"]

    found = await client.get("/api/projects", params={"q": target_title})
    assert found.status_code == 200, found.text
    assert [item["id"] for item in found.json()["items"]] == [target["id"]]
    assert found.json()["items"][0]["title"] == target_title


async def test_search_normalizes_whitespace_and_never_reads_message_body(app, client):
    alpha = await _create_project(client)
    visible = await _create_project(client)
    await _set_project_rows(
        app,
        [
            (alpha["id"], "Alpha Beta", "2026-01-02T00:00:00.000Z"),
            (visible["id"], "Visible", "2026-01-01T00:00:00.000Z"),
        ],
    )
    now = format_timestamp(utc_now())
    async with write_transaction(app.state.session_factory) as session:
        session.add(
            Message(
                id=str(uuid4()),
                project_id=visible["id"],
                sequence_no=1,
                role="user",
                content="secretneedle body text",
                project_revision=0,
                brief_snapshot_json=None,
                operation_id=None,
                created_at=now,
            )
        )
        await session.flush()

    normalized = await client.get("/api/projects", params={"q": "  ALPHA   BETA "})
    assert normalized.status_code == 200, normalized.text
    assert [item["id"] for item in normalized.json()["items"]] == [alpha["id"]]

    message_hit = await client.get("/api/projects", params={"q": "secretneedle"})
    assert message_hit.status_code == 200, message_hit.text
    assert message_hit.json()["items"] == []

    empty = await client.get("/api/projects", params={"q": "   "})
    assert empty.status_code == 200, empty.text
    assert [item["id"] for item in empty.json()["items"]] == [alpha["id"], visible["id"]]

    too_long = await client.get("/api/projects", params={"q": "x" * 121})
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_cursor_is_bound_to_query_and_owner(app, client):
    rows = []
    for index in range(3):
        item = await _create_project(client)
        rows.append((item["id"], f"Alpha {index}", f"2026-01-03T00:00:0{index}.000Z"))
    beta = await _create_project(client)
    rows.append((beta["id"], "Beta", "2026-01-01T00:00:00.000Z"))
    await _set_project_rows(app, rows)

    first = await client.get("/api/projects", params={"q": "alpha", "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    assert cursor

    same = await client.get("/api/projects", params={"q": "alpha", "limit": 1, "cursor": cursor})
    assert same.status_code == 200, same.text
    assert len(same.json()["items"]) == 1

    other_query = await client.get(
        "/api/projects", params={"q": "beta", "limit": 1, "cursor": cursor}
    )
    assert other_query.status_code == 422, other_query.text
    assert other_query.json()["error"]["code"] == "INVALID_CURSOR"

    decoded = decode_cursor(cursor)
    tampered = encode_cursor(
        decoded.updated_at,
        decoded.project_id,
        query=decoded.query,
        owner="other-owner",
    )
    other_owner = await client.get(
        "/api/projects", params={"q": "alpha", "limit": 1, "cursor": tampered}
    )
    assert other_owner.status_code == 422, other_owner.text
    assert other_owner.json()["error"]["code"] == "INVALID_CURSOR"


async def test_admin_search_and_rename_stay_inside_owner_boundary(app, client):
    project = await _create_project(client)
    await _set_project_rows(
        app, [(project["id"], "Other Account Secret", "2026-01-01T00:00:00.000Z")]
    )
    other_id = await _add_account(app, "other@example.com")
    async with write_transaction(app.state.session_factory) as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        row.owner_account_id = other_id
        await session.flush()

    listing = await client.get("/api/projects")
    assert listing.status_code == 200, listing.text
    assert project["id"] not in [item["id"] for item in listing.json()["items"]]

    search = await client.get("/api/projects", params={"q": "Other Account Secret"})
    assert search.status_code == 200, search.text
    assert search.json()["items"] == []

    rename = await client.patch(
        f"/api/projects/{project['id']}", json={"title": "stolen title"}
    )
    assert rename.status_code == 404, rename.text
    assert rename.json()["error"]["code"] == "PROJECT_NOT_FOUND"


async def test_rename_validation_and_csrf(app, client):
    project = await _create_project(client)

    blank = await client.patch(f"/api/projects/{project['id']}", json={"title": "   "})
    assert blank.status_code == 422, blank.text
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"

    long_title = await client.patch(
        f"/api/projects/{project['id']}", json={"title": "x" * 121}
    )
    assert long_title.status_code == 422, long_title.text
    assert long_title.json()["error"]["code"] == "VALIDATION_ERROR"

    missing = await client.patch(f"/api/projects/{project['id']}", json={})
    assert missing.status_code == 422, missing.text

    original_token = client.csrf_token
    client.csrf_token = None
    try:
        blocked = await client.patch(
            f"/api/projects/{project['id']}", json={"title": "Renamed"}
        )
        assert blocked.status_code == 403, blocked.text
    finally:
        client.csrf_token = original_token

    renamed = await client.patch(
        f"/api/projects/{project['id']}", json={"title": "  Renamed   project  "}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Renamed project"


async def test_rename_is_metadata_only_and_preserves_workflow_facts(full_settings):
    async def unexpected(_request):
        raise AssertionError("rename must not call a model")

    async with environment(
        full_settings, engineer=unexpected, reviewer=unexpected
    ) as (app, client, llm, _visual, _registry):
        setup = await setup_confirmed_project(app, client)
        project_id = setup["project_id"]
        artifact_id = setup["final_artifact_id"]
        async with read_session(app.state.session_factory) as session:
            artifact = await session.get(Artifact, artifact_id)
            assert artifact is not None
            approval = await session.scalar(
                select(Approval).where(Approval.artifact_id == artifact_id)
            )
            assert approval is not None
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == project_id)
            )
            assert run is not None
            project = await session.get(Project, project_id)
            assert project is not None
            owner_id = await _admin_id(app)
        before_project = {
            key: getattr(project, key)
            for key in (
                "revision",
                "prompt_revision",
                "confirmed_revision",
                "brief_status",
                "product_description",
                "purpose",
                "style",
                "composition",
                "text_content",
                "image_size",
                "reference_asset_id",
                "prompt",
            )
        }
        before_updated_at = project.updated_at
        before_artifact = (artifact.digest, artifact.payload_json, artifact.revision_no)
        before_approval = (
            approval.checks_json,
            approval.artifact_digest,
            approval.requirement_snapshot_digest,
        )

        now = format_timestamp(utc_now())
        async with write_transaction(app.state.session_factory) as session:
            session.add(
                RevisionBudget(
                    run_id=run.id, artifact_kind="prompt_candidate", used=1, max_allowed=3
                )
            )
            session.add(
                GenerationIntent(
                    id=str(uuid4()),
                    project_id=project_id,
                    run_id=run.id,
                    owner_account_id=owner_id,
                    operator_account_id=owner_id,
                    approval_id=approval.id,
                    request_id=str(uuid4()),
                    request_digest="i" * 64,
                    requirement_revision=project.confirmed_revision or 0,
                    artifact_id=artifact_id,
                    artifact_digest=artifact.digest,
                    reference_asset_id=project.reference_asset_id,
                    reference_usage="none",
                    image_size=project.image_size or "1024x1024",
                    status="accepted",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()

        async with read_session(app.state.session_factory) as session:
            intent = await session.scalar(
                select(GenerationIntent).where(GenerationIntent.project_id == project_id)
            )
            budget = await session.scalar(
                select(RevisionBudget).where(RevisionBudget.run_id == run.id)
            )
            assert intent is not None and budget is not None
            before_intent = (
                intent.status,
                intent.artifact_digest,
                intent.request_digest,
                intent.approval_id,
            )
            before_budget = (budget.used, budget.max_allowed)
        assert len(llm.role_calls_received) == 0

        await asyncio.sleep(0.002)
        response = await client.patch(
            f"/api/projects/{project_id}", json={"title": "  Renamed   project  "}
        )
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Renamed project"

        async with read_session(app.state.session_factory) as session:
            after_project = await session.get(Project, project_id)
            after_artifact = await session.get(Artifact, artifact_id)
            after_approval = await session.get(Approval, approval.id)
            after_intent = await session.scalar(
                select(GenerationIntent).where(GenerationIntent.project_id == project_id)
            )
            after_budget = await session.scalar(
                select(RevisionBudget).where(RevisionBudget.run_id == run.id)
            )
        assert after_project is not None
        assert after_artifact is not None and after_approval is not None
        assert after_intent is not None and after_budget is not None
        assert {key: getattr(after_project, key) for key in before_project} == before_project
        assert after_project.updated_at != before_updated_at
        assert (
            after_artifact.digest,
            after_artifact.payload_json,
            after_artifact.revision_no,
        ) == before_artifact
        assert (
            after_approval.checks_json,
            after_approval.artifact_digest,
            after_approval.requirement_snapshot_digest,
        ) == before_approval
        assert (
            after_intent.status,
            after_intent.artifact_digest,
            after_intent.request_digest,
            after_intent.approval_id,
        ) == before_intent
        assert (after_budget.used, after_budget.max_allowed) == before_budget
        assert len(llm.role_calls_received) == 0


async def test_list_status_uses_current_confirmed_revision_only(app, client):
    project = await _create_project(client)
    await _set_project_rows(
        app, [(project["id"], "Status Project", "2026-01-01T00:00:00.000Z")]
    )
    owner_id = await _admin_id(app)
    async with write_transaction(app.state.session_factory) as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        row.revision = 1
        await session.flush()
    await _add_generation(
        app,
        project_id=project["id"],
        owner_id=owner_id,
        version_no=1,
        confirmed_revision=0,
        status="succeeded",
    )

    stale = await client.get("/api/projects", params={"q": "Status Project"})
    assert stale.status_code == 200, stale.text
    assert stale.json()["items"][0]["latest_generation_status"] == "unknown"

    await _add_generation(
        app,
        project_id=project["id"],
        owner_id=owner_id,
        version_no=2,
        confirmed_revision=1,
        status="running",
    )
    current = await client.get("/api/projects", params={"q": "Status Project"})
    assert current.status_code == 200, current.text
    assert current.json()["items"][0]["latest_generation_status"] == "running"
