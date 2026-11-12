from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.db import read_session, write_transaction
from app.models import Asset, ImageCorrection
from app.services.assets import resolve_asset_file
from app.workflow.tables import GenerationJob
from tests.support.adapters import LLMStub, VisualStub
from tests.support.t02_prompt import final_click, ready_project, responder
from tests.test_t62_background_ownership import _add_account, _login_user
from tests.test_t65_generation_cycle import _drain, _environment, _quality_response
from tests.test_v2_t40_prompt_review import png_bytes


async def _view(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _act(client, project_id: str, action: str, payload: dict) -> object:
    current = await _view(client, project_id)
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/{action}",
        json={"expected_revision": current["project_revision"], "payload": payload},
    )


async def _jobs(app, project_id: str) -> list[GenerationJob]:
    async with read_session(app.state.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(GenerationJob)
                    .where(GenerationJob.project_id == project_id)
                    .order_by(GenerationJob.created_at)
                )
            ).all()
        )


async def _corrections(app, project_id: str) -> list[ImageCorrection]:
    async with read_session(app.state.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(ImageCorrection)
                    .where(ImageCorrection.project_id == project_id)
                    .order_by(ImageCorrection.created_at)
                )
            ).all()
        )


def _quality_fails_once():
    state = {"calls": 0}

    def inner(request):
        if request.payload["task"]["role"] == "quality_compliance":
            state["calls"] += 1
            if state["calls"] == 1:
                return _quality_response(request, decision="fail")
        return responder(request)

    inner.state = state
    return inner


def _engineer_applies_note():
    def inner(request):
        result = responder(request)
        if request.payload["task"]["role"] == "generation_engineer":
            note = request.payload["input_data"].get("revision_note")
            if note:
                result["data"]["positive_prompt"] += "；" + note
        return result

    return inner


@pytest.mark.asyncio
async def test_correction_is_a_registered_reviewed_execution_fact(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    visual.submit_outcome = ProviderOutcome(
        state="succeeded", image_bytes=png_bytes(1024, 1024), evidence="submitted"
    )
    state = {"quality_calls": 0}

    def routed(request):
        if request.payload["task"]["role"] == "quality_compliance":
            state["quality_calls"] += 1
            if state["quality_calls"] == 1:
                return _quality_response(request, decision="fail")
        result = responder(request)
        if request.payload["task"]["role"] == "generation_engineer":
            note = request.payload["input_data"].get("revision_note")
            if note:
                result["data"]["positive_prompt"] += "；" + note
        return result

    llm.role_responder = routed

    async with _environment(
        full_settings, llm, visual, responder=routed
    ) as (app, client):
        setup = await ready_project(app, client)
        project_id = setup["project_id"]
        await final_click(client, setup)
        await _drain(app)
        view = await _view(client, project_id)
        assert view["stage"] == "S09"
        assert view["issue"]["code"] == "IMAGE_NOT_APPROVED"
        first_jobs = await _jobs(app, project_id)
        assert len(first_jobs) == 1
        original = first_jobs[0]
        assert len(visual.submit_calls) == 1
        async with read_session(app.state.session_factory) as session:
            source = await session.get(Asset, original.result_asset_id)
        assert source is not None and source.sha256
        source_file = resolve_asset_file(app.state.settings, source)
        assert source_file is not None
        source_bytes = source_file.read_bytes()

        response = await _act(
            client,
            project_id,
            "request_correction",
            {"text": "去除图片中的文字水印，保留商品主体"},
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S07"
        assert view["status"] == "needs_input"
        assert view["prompt_preview"] is not None
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        corrections = await _corrections(app, project_id)
        assert len(corrections) == 1
        correction = corrections[0]
        assert correction.source_job_id == original.id
        assert correction.source_asset_id == original.result_asset_id
        assert correction.source_asset_sha256 == source.sha256
        assert correction.status in {"prepared", "authorized"}

        click = await final_click(
            client,
            {
                "project_id": project_id,
                "revision": view["project_revision"],
                "workflow": view,
            },
            command_id=str(uuid.uuid4()),
        )
        assert click.status_code == 200, click.text
        await _drain(app)
        view = await _view(client, project_id)
        assert view["stage"] == "S10", view
        assert len(visual.submit_calls) == 2
        submitted = visual.submit_calls[1]
        assert submitted.reference_bytes == source_bytes
        assert "去除图片中的文字水印" in submitted.prompt
        second_jobs = await _jobs(app, project_id)
        assert len(second_jobs) == 2
        assert second_jobs[0].result_asset_id == original.result_asset_id
        assert second_jobs[1].result_asset_id
        corrections = await _corrections(app, project_id)
        assert corrections[0].status == "approved"
        assert corrections[0].result_asset_id == second_jobs[1].result_asset_id


@pytest.mark.asyncio
async def test_recheck_rejects_an_arbitrary_or_foreign_asset(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    visual.submit_outcome = ProviderOutcome(
        state="succeeded", image_bytes=png_bytes(1024, 1024), evidence="submitted"
    )
    responder_once = _quality_fails_once()

    async with _environment(
        full_settings, llm, visual, responder=responder_once
    ) as (app, client):
        setup = await ready_project(app, client)
        project_id = setup["project_id"]
        await final_click(client, setup)
        await _drain(app)
        view = await _view(client, project_id)
        assert view["issue"]["code"] == "IMAGE_NOT_APPROVED"

        response = await _act(
            client,
            project_id,
            "recheck_candidate",
            {"asset_id": str(uuid.uuid4())},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CORRECTION_NOT_REGISTERED"

        response = await _act(
            client,
            project_id,
            "recheck_candidate",
            {"correction_id": str(uuid.uuid4())},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CORRECTION_NOT_REGISTERED"
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_correction_cannot_cross_tasks_or_survive_byte_replacement(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    visual.submit_outcome = ProviderOutcome(
        state="succeeded", image_bytes=png_bytes(1024, 1024), evidence="submitted"
    )
    state = {"quality_calls": 0}

    def routed(request):
        if request.payload["task"]["role"] == "quality_compliance":
            state["quality_calls"] += 1
            if state["quality_calls"] <= 3:
                return _quality_response(request, decision="fail")
        result = responder(request)
        if request.payload["task"]["role"] == "generation_engineer":
            note = request.payload["input_data"].get("revision_note")
            if note:
                result["data"]["positive_prompt"] += "；" + note
        return result

    async with _environment(
        full_settings, llm, visual, responder=routed
    ) as (app, client):
        first = await ready_project(app, client)
        await _add_account(app, "correction-other@example.com")
        other = await _login_user(app, "correction-other@example.com")
        second = await ready_project(app, other)
        await final_click(client, first)
        await final_click(other, second)
        await _drain(app)
        first_view = await _view(client, first["project_id"])
        second_view = await _view(other, second["project_id"])
        assert first_view["issue"]["code"] == "IMAGE_NOT_APPROVED"
        assert second_view["issue"]["code"] == "IMAGE_NOT_APPROVED"

        response = await _act(
            other,
            second["project_id"],
            "request_correction",
            {"text": "把背景改成浅灰色"},
        )
        assert response.status_code == 200, response.text
        foreign_correction = (await _corrections(app, second["project_id"]))[0]

        response = await _act(
            client,
            first["project_id"],
            "recheck_candidate",
            {"correction_id": foreign_correction.id},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CORRECTION_NOT_REGISTERED"

        response = await _act(
            client,
            first["project_id"],
            "request_correction",
            {"text": "去除水印并保留商品主体"},
        )
        assert response.status_code == 200, response.text
        view = response.json()
        click = await final_click(
            client,
            {
                "project_id": first["project_id"],
                "revision": view["project_revision"],
                "workflow": view,
            },
            command_id=str(uuid.uuid4()),
        )
        assert click.status_code == 200, click.text
        await _drain(app)
        view = await _view(client, first["project_id"])
        assert view["issue"]["code"] == "IMAGE_NOT_APPROVED"

        correction = (await _corrections(app, first["project_id"]))[0]
        assert correction.status == "rejected"
        assert correction.result_asset_id and correction.result_asset_sha256
        async with read_session(app.state.session_factory) as session:
            asset = await session.get(Asset, correction.result_asset_id)
            job = await session.get(GenerationJob, correction.correction_job_id)
        assert asset is not None and job is not None
        path = resolve_asset_file(app.state.settings, asset)
        assert path is not None
        path.write_bytes(png_bytes(1024, 1024, color=(1, 2, 3)))

        async with write_transaction(app.state.session_factory) as session:
            stored = await session.get(ImageCorrection, correction.id)
            stored_job = await session.get(GenerationJob, correction.correction_job_id)
            assert stored is not None and stored_job is not None
            stored.status = "reviewing"
            stored_job.status = "quarantined"

        response = await _act(
            client,
            first["project_id"],
            "recheck_candidate",
            {"correction_id": correction.id},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CORRECTION_NOT_REGISTERED"
