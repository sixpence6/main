from __future__ import annotations

import uuid

import pytest

from app.workflow import templates
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import _drain, _environment, _rows, _setup_s06


@pytest.mark.asyncio
async def test_bare_typed_confirmation_confirms_requirements_without_extra_turn(
    full_settings,
) -> None:
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/messages",
            json={
                "content": "确认",
                "expected_revision": setup["revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["user_message"]["turn_kind"] == "workflow"
        assert payload["assistant_message"]["content"] == templates.render(
            "REQUIREMENT_CONFIRMED"
        )
        assert payload["workflow"]["stage"] == "S07"
        assert not any(
            item.payload["task"]["role"] == "conversation_router"
            for item in llm.role_calls_received
        )

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "accepted"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        await _drain(app)
        assert not any(
            item.payload["task"]["role"] == "conversation_router"
            for item in llm.role_calls_received
        )
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S07"
        assert view["status"] == "needs_input"
        assert view["prompt_preview"] is not None
        assert view["prompt_preview"]["can_confirm"] is True
        assert visual.submit_calls == []
