"""Review regressions through authenticated messages and persisted workflow state."""

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models import Project
from app.workflow.stages import s02
from app.workflow.tables import WorkflowRun
from tests.test_chat_intent_routing import production_calls, snapshot
from tests.test_collaborative_revisions import answer, send, settings_for
from tests.test_conversation_alignment import first
from tests.test_markdown_handoff import MarkdownScript
from tests.test_role_chain_consolidation import ConsolidatedScript
from tests.test_t65_generation_cycle import _drain
from tests.test_v2_t04_reference_analysis import png_bytes
from tests.test_v2_t05_requirements import _create_project, _environment, _messages, _view


async def persisted_requirements(app, project_id):
    async with app.state.session_factory() as session:
        run = await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id))
        assert run is not None
        return json.loads(run.requirements_state_json)


async def start_pending(app, client, project_id):
    response = await send(client, project_id, "帮我做钢笔宣传图，送礼用")
    assert response.status_code == 200, response.text
    await _drain(app)
    view = await _view(client, project_id)
    assert view["conversation_question"]["field"] == "style_keywords"
    state = await persisted_requirements(app, project_id)
    assert state["generation_intent"]["status"] == "requested"
    assert state["questions"]
    return view


@pytest.mark.parametrize(
    "text",
    [
        "文案为‘新品上市’，先别生成",
        "文案为‘新品上市’，先讨论",
        "先别生成，文案为‘新品上市’",
        "先讨论，文案为‘新品上市’",
        "文案为‘新品上市’，我们先讨论一下背景",
        "请先讨论背景，文案为‘新品上市’",
    ],
)
async def test_copy_declaration_does_not_swallow_pause(full_settings, text):
    script = ConsolidatedScript(answers=[first])
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        await start_pending(app, client, project_id)
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "conversation"
        await _drain(app)
        state = await persisted_requirements(app, project_id)
        assert state["generation_intent"]["status"] == "paused", state
        assert state["generation_intent"]["message_id"] == response.json()["user_message"]["id"]
        view = await _view(client, project_id)
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert not view["prompt_preview"]
        assert production_calls(llm) == []
        assert visual.submit_calls == []
        # A later social turn and HTTP refresh must not revive the old request.
        followup = await send(client, project_id, "谢谢")
        assert followup.status_code == 200, followup.text
        assert (await persisted_requirements(app, project_id))["generation_intent"] == state[
            "generation_intent"
        ]
        assert production_calls(llm) == []
        concrete = await send(client, project_id, "冷白背景")
        assert concrete.status_code == 200, concrete.text
        await _drain(app)
        assert (await persisted_requirements(app, project_id))["generation_intent"]["status"] == "paused"
        assert production_calls(llm) == []
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "text",
    [
        "制作钢笔电商主图，极简冷白背景，文案为‘先别生成’",
        "制作钢笔电商主图，极简冷白背景，图中文字‘先讨论’",
    ],
)
async def test_quoted_pause_words_are_image_copy_not_instructions(full_settings, text):
    copy = "先别生成" if "先别生成" in text else "先讨论"

    def ready(request):
        return answer(
            request, kind="requirements_change", product_description="钢笔",
            style_keywords=["极简", "冷白背景"], texts=[copy],
        )

    script = ConsolidatedScript(answers=[ready])
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        await _drain(app)
        state = await persisted_requirements(app, project_id)
        assert state["generation_intent"]["status"] == "requested", state
        assert state["data"]["texts"] == [copy]
        view = await _view(client, project_id)
        assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"]
        assert production_calls(llm) == ["visual_planner", "generation_engineer"]
        assert visual.submit_calls == []


@pytest.mark.parametrize("text", ["我很喜欢冷白背景", "我们更喜欢冷白背景"])
async def test_concrete_preference_answers_persisted_background_question(full_settings, text):
    def clarified(request):
        return answer(
            request, product_description="钢笔", purpose="商务送礼", style_keywords=["冷白背景"]
        )

    script = ConsolidatedScript(answers=[first, clarified])
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        before = await start_pending(app, client, project_id)
        before_calls = len(llm.role_calls_received)
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "workflow"
        await _drain(app)
        state = await persisted_requirements(app, project_id)
        assert state["generation_intent"]["status"] == "requested"
        assert state["questions"] == []
        assert "冷白背景" in str(state["data"]["style_keywords"])
        view = await _view(client, project_id)
        assert view["requirement_revision"] > before["requirement_revision"]
        assert view["conversation_question"] is None
        assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"]
        assert [r.payload["task"]["role"] for r in llm.role_calls_received[before_calls:]] == [
            "requirements_assistant", "visual_planner", "generation_engineer",
        ]
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "pending,text",
    [
        (True, "我很喜欢这个背景"),
        (True, "我们更喜欢那个背景"),
        (False, "我很喜欢冷白背景"),
        (False, "我们更喜欢冷白背景"),
    ],
)
async def test_unbound_preferences_do_not_authorize_production(full_settings, pending, text):
    # A model willing to return a filled brief must not override missing authority.
    script = ConsolidatedScript(answers=[first] if pending else None)
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        if pending:
            await start_pending(app, client, project_id)
        before = await snapshot(app, client, project_id)
        before_calls = len(llm.role_calls_received)
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "conversation"
        await _drain(app)
        assert await snapshot(app, client, project_id) == before
        expected_roles = ["requirements_assistant"] if pending else []
        assert [r.payload["task"]["role"] for r in llm.role_calls_received[before_calls:]] == (
            expected_roles
        )
        assert production_calls(llm) == []
        assert visual.submit_calls == []


@pytest.mark.parametrize("phase", ["upload_only", "prompt"])
@pytest.mark.parametrize("text", ["你好", "谢谢你，帮大忙了"])
async def test_markdown_social_message_preserves_bound_reference(
    full_settings, monkeypatch, phase, text
):
    # Leave the default handoff format in place: this caught a Markdown-only failure.
    settings = settings_for(full_settings)
    assert settings.workflow_handoff_format == "markdown"
    script = MarkdownScript()
    async with _environment(settings, script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        uploaded = await client.post(
            f"/api/projects/{project_id}/reference",
            files={"file": ("pen.png", png_bytes(), "image/png")},
            data={"expected_revision": "0"},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["reference_asset"]["id"]
        if phase == "prompt":
            started = await send(
                client, project_id, "帮我做钢笔电商主图，浅色背景，不需要文字"
            )
            assert started.status_code == 200, started.text
            await _drain(app)
            assert (await _view(client, project_id))["prompt_preview"]["can_confirm"]
        before_calls = len(llm.role_calls_received)
        before = await snapshot(app, client, project_id)
        history = await _messages(client, project_id)
        prepare = AsyncMock(wraps=s02.prepare_after_reference_change)
        monkeypatch.setattr(s02, "prepare_after_reference_change", prepare)
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_message"]["turn_kind"] == "conversation"
        assert body["assistant_message"]["turn_kind"] == "conversation"
        await _drain(app)
        assert await snapshot(app, client, project_id) == before
        async with app.state.session_factory() as session:
            project = await session.get(Project, project_id)
            assert project.reference_asset_id == asset_id
        after_history = await _messages(client, project_id)
        assert len(after_history) == len(history) + 2
        assert after_history[-2]["content"] == text
        assert after_history[-1]["content"]
        expected_roles = ["requirements_assistant"] if phase == "prompt" else []
        assert [r.payload["task"]["role"] for r in llm.role_calls_received[before_calls:]] == (
            expected_roles
        )
        assert all(r.output_format == "markdown" for r in script.calls)
        assert llm.calls == []
        prepare.assert_not_called()
        prepare.assert_not_awaited()
        assert visual.submit_calls == []

