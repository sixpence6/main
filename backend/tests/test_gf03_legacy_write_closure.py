"""GF-03: the legacy confirmation/generation write entrances are tombstones.

These tests exercise the real HTTP routes with a fresh project (no WorkflowRun
and no legacy items) because that is exactly the case the old access policy
used to let through.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import aclosing, asynccontextmanager

import pytest
from httpx import ASGITransport

from app.config import Settings
from app.main import create_app
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate
from tests.support.auth import AnonymousClient, authenticated_client
from tests.test_t04_brief import create_project, review_responder, send
from tests.test_t62_background_ownership import _add_account, _login_user

LEGACY_ERROR = "LEGACY_FLOW_DISABLED"
CONFIRM = "/api/projects/{project_id}/confirm"
GENERATIONS = "/api/projects/{project_id}/generations"


@asynccontextmanager
async def _environment(settings: Settings):
    llm = LLMStub(responder=review_responder())
    visual = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, llm, visual


async def _reviewed_project(client) -> dict:
    created = await create_project(client)
    exchange = await send(client, created["id"], "做一个白色马克杯的电商主图", 0)
    project = exchange["project"]
    assert project["brief_status"] == "review"
    return project


def _snapshot(settings: Settings, project_id: str) -> dict[str, object]:
    with sqlite3.connect(settings.database_path) as db:
        project = db.execute(
            "SELECT revision, brief_status, confirmed_revision, prompt_revision, updated_at "
            "FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        counts = {}
        for table in (
            "messages",
            "generations",
            "generation_jobs",
            "generation_intents",
            "generation_usage",
            "generation_slots",
            "approvals",
            "prepared_prompts",
            "final_authorizations",
        ):
            if table == "generation_slots":
                counts[table] = db.execute(
                    "SELECT COUNT(*) FROM generation_slots s "
                    "JOIN generation_intents i ON i.id=s.intent_id WHERE i.project_id=?",
                    (project_id,),
                ).fetchone()[0]
            else:
                counts[table] = db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id=?",
                    (project_id,),
                ).fetchone()[0]
    return {"project": project, "counts": counts}


def _assert_legacy_disabled(response) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == LEGACY_ERROR, response.text


def _assert_no_calls(llm: LLMStub, visual: VisualStub, before: tuple[int, int, int]) -> None:
    assert (len(llm.calls), len(llm.role_calls_received), len(visual.submit_calls)) == before


@pytest.mark.asyncio
async def test_fresh_project_legacy_confirm_and_generation_are_side_effect_free_tombstones(
    full_settings,
):
    async with _environment(full_settings) as (app, client, llm, visual):
        project = await _reviewed_project(client)
        before = _snapshot(full_settings, project["id"])
        calls_before = (len(llm.calls), len(llm.role_calls_received), len(visual.submit_calls))

        confirm_url = CONFIRM.format(project_id=project["id"])
        # No body at all, an empty body, the shape the old client used, and a
        # forged approval payload all reach the same stable tombstone.
        confirm_attempts = [
            await client.post(confirm_url),
            await client.post(confirm_url, json={}),
            await client.post(confirm_url, json={"revision": project["revision"]}),
            await client.post(
                confirm_url,
                json={
                    "revision": 0,
                    "approval_id": "forged-approval",
                    "approved": True,
                    "force": True,
                },
            ),
        ]
        for response in confirm_attempts:
            _assert_legacy_disabled(response)

        generation_url = GENERATIONS.format(project_id=project["id"])
        request_id = str(uuid.uuid4())
        generation_attempts = [
            await client.post(generation_url),
            await client.post(generation_url, json={}),
            await client.post(
                generation_url,
                json={"request_id": request_id, "confirmed_revision": project["revision"]},
            ),
            # Same request id: a replay is still a tombstone, never an old job.
            await client.post(
                generation_url,
                json={"request_id": request_id, "confirmed_revision": project["revision"]},
            ),
            await client.post(
                generation_url,
                json={
                    "request_id": str(uuid.uuid4()),
                    "confirmed_revision": 0,
                    "acknowledge_unknown": True,
                    "approval_id": "forged-approval",
                    "force": True,
                },
            ),
        ]
        for response in generation_attempts:
            _assert_legacy_disabled(response)

        assert _snapshot(full_settings, project["id"]) == before
        _assert_no_calls(llm, visual, calls_before)


@pytest.mark.asyncio
async def test_history_reads_and_unknown_retrieval_remain_read_only_after_closure(full_settings):
    async with _environment(full_settings) as (app, client, llm, visual):
        project = await _reviewed_project(client)
        before = _snapshot(full_settings, project["id"])
        calls_before = (len(llm.calls), len(llm.role_calls_received), len(visual.submit_calls))

        detail = await client.get(f"/api/projects/{project['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["project"]["brief_status"] == "review"

        unknown = uuid.uuid4()
        retrieved = await client.post(f"/api/generations/{unknown}/retrieve-result", json={})
        assert retrieved.status_code == 404, retrieved.text

        anonymous = AnonymousClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        )
        async with anonymous:
            denied = await anonymous.post(
                CONFIRM.format(project_id=project["id"]), json={"revision": project["revision"]}
            )
        assert denied.status_code == 401, denied.text

        assert _snapshot(full_settings, project["id"]) == before
        _assert_no_calls(llm, visual, calls_before)


@pytest.mark.asyncio
async def test_disabled_account_cannot_reach_legacy_tombstones(full_settings):
    async with _environment(full_settings) as (app, admin, llm, visual):
        owner = await _add_account(app, "gf03-disabled@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as owner_client:
            project = await _reviewed_project(owner_client)
            before = _snapshot(full_settings, project["id"])
            calls_before = (len(llm.calls), len(llm.role_calls_received), len(visual.submit_calls))

            disabled = await admin.post(f"/api/admin/accounts/{owner.id}/disable")
            assert disabled.status_code == 200, disabled.text

            confirm = await owner_client.post(
                CONFIRM.format(project_id=project["id"]),
                json={"revision": project["revision"]},
            )
            generation = await owner_client.post(
                GENERATIONS.format(project_id=project["id"]),
                json={"request_id": str(uuid.uuid4()), "confirmed_revision": project["revision"]},
            )

        for response in (confirm, generation):
            assert response.status_code == 401, response.text
            assert response.json()["error"]["code"] == "AUTH_REQUIRED"
        assert _snapshot(full_settings, project["id"]) == before
        _assert_no_calls(llm, visual, calls_before)
