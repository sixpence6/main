from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import create_app
from app.workflow import prompts, state, templates
from app.workflow.roles import run_role
from app.workflow.tables import RoleCall
from tests.conftest import migrate
from tests.support.adapters import LLMStub, build_test_registry
from tests.support.auth import authenticated_client


def intent_response() -> dict:
    return {
        "role": "intent_router",
        "status": "ready",
        "data": {
            "route": "image_request",
            "action": "new_request",
            "evidence_ids": [],
        },
        "issues": [],
        "questions": [],
    }


async def test_workflow_view_starts_empty_and_offers_submit(llm_client):
    project = (await llm_client.post("/api/projects", json={})).json()
    response = await llm_client.get(f"/api/projects/{project['id']}/workflow")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "none"
    assert body["project_revision"] == 0
    assert [action["id"] for action in body["available_actions"]] == ["submit_request"]


async def test_role_call_is_single_attempt_and_persisted(llm_settings):
    stub = LLMStub(role_responses={"intent_router": [intent_response()]})
    registry = build_test_registry(llm=stub)
    migrate(llm_settings)
    application = create_app(settings=llm_settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            project = (await client.post("/api/projects", json={})).json()

        ctx = await state.load_context(application, project["id"])
        await state.create_run(ctx)
        ctx = await state.reload_context(ctx)
        output = await run_role(
            ctx,
            "intent_router",
            "S01",
            {"message": "做一张商品图"},
        )
        assert output.status == "ready"
        assert output.data is not None
        assert len(stub.role_calls_received) == 1
        system_prompt = stub.role_calls_received[0].system_prompt
        assert prompts.SYS_BASE in system_prompt
        assert "v2.0.0" in system_prompt
        payload = stub.role_calls_received[0].payload
        assert payload["task"]["role"] == "intent_router"
        assert payload["input_data"] == {"message": "做一张商品图"}

        async with application.state.session_factory() as session:
            calls = (await session.scalars(select(RoleCall))).all()
        assert len(calls) == 1
        assert calls[0].status == "ok"
        assert calls[0].attempt == 1
        assert calls[0].model_fingerprint == "stub-llm:intent_router:deadbeef"


def test_templates_are_exact_and_fail_closed():
    assert templates.render("SCOPE_REFUSAL") == "抱歉无法回答此内容"
    assert templates.render("WAIT_STAGE", stage_label="整理需求") == "正在整理需求，请稍候。"
    assert templates.render("NOT_A_TEMPLATE") == templates.render("SERVICE_UNAVAILABLE")
    assert templates.render("WAIT_STAGE", wrong="x") == templates.render("SERVICE_UNAVAILABLE")
