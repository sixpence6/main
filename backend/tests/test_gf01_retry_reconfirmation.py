from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.db import read_session
from app.final_confirmation import FinalAuthorization
from app.workflow.tables import GenerationJob
from tests.support.adapters import LLMStub, VisualStub
from tests.support.t02_prompt import final_click, ready_project
from tests.support.t02_prompt import responder as prompt_responder
from tests.test_t65_generation_cycle import (
    _drain,
    _environment,
    _quality_response,
    _rows,
)
from tests.test_v2_t40_prompt_review import png_bytes


async def _view(client, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _act(
    client,
    project_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    text: str | None = None,
):
    view = await _view(client, project_id)
    body: dict[str, Any] = {"expected_revision": view["project_revision"]}
    if payload is not None:
        body["payload"] = payload
    if text is not None:
        body["text"] = text
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/{action}", json=body
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


async def _authorizations(app, project_id: str) -> list[FinalAuthorization]:
    async with read_session(app.state.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(FinalAuthorization)
                    .where(FinalAuthorization.project_id == project_id)
                    .order_by(FinalAuthorization.created_at)
                )
            ).all()
        )


def _assert_prepared_prompt(view: dict[str, Any], *required: str) -> str:
    preview = view["prompt_preview"]
    assert preview is not None, view
    request = preview["request"]
    assert request["negative_prompt"] == "价格标签；促销文案"
    prompt = request["prompt"]
    assert "禁止内容（Negative prompt）" in prompt
    assert "价格标签；促销文案" in prompt
    for part in required:
        assert part in prompt, prompt
    return prompt


@pytest.mark.asyncio
async def test_terminal_failure_retry_rebuilds_prompt_and_requires_final_confirmation(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed",
            error_code="VISUAL_TASK_FAILED",
            error_message="图片服务返回失败",
        )
    )
    async with _environment(full_settings, llm, visual, prompt_responder) as (app, client):
        setup = await ready_project(app, client)
        project_id = setup["project_id"]
        old_preview = setup["workflow"]["prompt_preview"]
        assert old_preview is not None

        first = await final_click(client, setup)
        assert first.status_code == 200, first.text
        await _drain(app)

        view = await _view(client, project_id)
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "GENERATION_FAILED"
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1
        intents_before, usages_before, slots_before = await _rows(app, project_id)
        assert len(intents_before) == 1
        assert len(usages_before) == 1
        assert len(slots_before) == 3
        assert all(slot.released_at is not None for slot in slots_before)
        calls_before = len(llm.role_calls_received)

        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        retried = await _act(
            client,
            project_id,
            "retry_generation",
            {"acknowledge_new_attempt": True},
        )
        assert retried.status_code == 200, retried.text
        await _drain(app)

        view = await _view(client, project_id)
        assert view["stage"] == "S07"
        assert view["status"] == "needs_input"
        _assert_prepared_prompt(view)
        assert len(llm.role_calls_received) == calls_before + 2
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1
        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 2
        assert intents[0].status == "failed"
        assert intents[1].status == "accepted"
        assert len(usages) == 2
        assert len(slots) == 6
        assert all(slot.released_at is not None for slot in slots[:3])
        assert all(slot.released_at is None for slot in slots[3:])

        old_setup = {
            "project_id": project_id,
            "workflow": {
                "project_revision": view["project_revision"],
                "prompt_preview": old_preview,
            },
        }
        stale = await final_click(client, old_setup)
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "PROMPT_STALE"
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1

        current = await _view(client, project_id)
        confirmed = await final_click(
            client,
            {
                "project_id": project_id,
                "workflow": current,
            },
            command_id=str(uuid.uuid4()),
        )
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        final_view = await _view(client, project_id)
        assert final_view["stage"] == "S10"
        assert final_view["status"] == "needs_input"
        assert len(visual.submit_calls) == 2
        assert "禁止内容（Negative prompt）" in visual.submit_calls[-1].prompt
        assert "价格标签；促销文案" in visual.submit_calls[-1].prompt
        assert len(await _jobs(app, project_id)) == 2
        assert len(await _authorizations(app, project_id)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "text"),
    [
        ("retry_rejected_generation", None),
        ("revise_prompt_and_retry", "将背景改为浅灰色，保留商品主体"),
    ],
)
async def test_quality_retry_rebuilds_prompt_and_requires_final_confirmation(
    full_settings, action: str, text: str | None
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    quality_calls = {"count": 0}
    engineer_calls = {"count": 0}

    def responder(request):
        role = request.payload["task"]["role"]
        if role == "quality_compliance":
            quality_calls["count"] += 1
            return _quality_response(
                request,
                decision="fail" if quality_calls["count"] == 1 else "pass",
            )
        base = prompt_responder(request)
        if role == "generation_engineer":
            engineer_calls["count"] += 1
            if text and engineer_calls["count"] > 1:
                base["data"]["positive_prompt"] += f"；{text}"
        return base

    async with _environment(full_settings, llm, visual, responder) as (app, client):
        setup = await ready_project(app, client)
        project_id = setup["project_id"]
        first = await final_click(client, setup)
        assert first.status_code == 200, first.text
        await _drain(app)

        view = await _view(client, project_id)
        assert view["stage"] == "S09"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "IMAGE_NOT_APPROVED"
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1
        calls_before = len(llm.role_calls_received)

        retried = await _act(
            client,
            project_id,
            action,
            {"acknowledge_new_attempt": True},
            text=text,
        )
        assert retried.status_code == 200, retried.text
        await _drain(app)

        view = await _view(client, project_id)
        assert view["stage"] == "S07"
        assert view["status"] == "needs_input"
        required = (text,) if text else ()
        _assert_prepared_prompt(view, *required)
        assert len(llm.role_calls_received) == calls_before + 2
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1
        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 2
        assert len(usages) == 2
        assert len(slots) == 6

        current = await _view(client, project_id)
        confirmed = await final_click(
            client,
            {"project_id": project_id, "workflow": current},
            command_id=str(uuid.uuid4()),
        )
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        final_view = await _view(client, project_id)
        assert final_view["stage"] == "S10"
        assert final_view["status"] == "needs_input"
        assert len(visual.submit_calls) == 2
        assert "禁止内容（Negative prompt）" in visual.submit_calls[-1].prompt
        if text:
            assert text in visual.submit_calls[-1].prompt
        assert len(await _jobs(app, project_id)) == 2
        assert len(await _authorizations(app, project_id)) == 2


@pytest.mark.asyncio
async def test_unknown_closed_task_new_generation_rebuilds_prompt_before_execution(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed",
            evidence="result_available",
            error_code="RESULT_FETCH_FAILED",
        )
    )
    async with _environment(full_settings, llm, visual, prompt_responder) as (app, client):
        setup = await ready_project(app, client)
        project_id = setup["project_id"]
        first = await final_click(client, setup)
        assert first.status_code == 200, first.text
        await _drain(app)

        view = await _view(client, project_id)
        assert view["stage"] == "S08"
        assert view["issue"]["code"] == "RESULT_RETRIEVAL_PENDING"
        assert len(visual.submit_calls) == 1

        recovered = await _act(client, project_id, "recover_generation")
        assert recovered.status_code == 200, recovered.text
        view = await _view(client, project_id)
        assert view["issue"]["code"] == "GENERATION_UNKNOWN"

        closed = await _act(
            client,
            project_id,
            "close_generation_tracking",
            {"acknowledge_local_close": True},
        )
        assert closed.status_code == 200, closed.text
        view = await _view(client, project_id)
        assert view["issue"]["code"] == "LOCAL_TRACKING_CLOSED"
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1
        calls_before = len(llm.role_calls_received)

        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        started = await _act(
            client,
            project_id,
            "start_new_generation",
            {"acknowledge_risk": True},
        )
        assert started.status_code == 200, started.text
        await _drain(app)

        view = await _view(client, project_id)
        assert view["stage"] == "S07"
        assert view["status"] == "needs_input"
        _assert_prepared_prompt(view)
        assert len(llm.role_calls_received) == calls_before + 2
        assert len(visual.submit_calls) == 1
        assert len(await _jobs(app, project_id)) == 1
        assert len(await _authorizations(app, project_id)) == 1
        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 2
        assert len(usages) == 2
        assert len(slots) == 6

        current = await _view(client, project_id)
        confirmed = await final_click(
            client,
            {"project_id": project_id, "workflow": current},
            command_id=str(uuid.uuid4()),
        )
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        final_view = await _view(client, project_id)
        assert final_view["stage"] == "S10"
        assert final_view["status"] == "needs_input"
        assert len(visual.submit_calls) == 2
        assert len(await _jobs(app, project_id)) == 2
        assert len(await _authorizations(app, project_id)) == 2
