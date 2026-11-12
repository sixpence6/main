from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.adapters.base import ProviderOutcome
from app.final_confirmation import FinalAuthorization
from app.prompt_preparation import PreparedPrompt
from app.workflow.tables import GenerationJob
from tests.test_collaborative_revisions import settings_for
from tests.test_role_chain_consolidation import ConsolidatedScript
from tests.test_t65_generation_cycle import _drain
from tests.test_v2_t05_requirements import _create_project, _environment, _start, _view
from tests.test_v2_t40_prompt_review import png_bytes


async def action(client, project_id, view, name, payload):
    return await client.post(f"/api/projects/{project_id}/workflow/actions/{name}",
                             json={"expected_revision": view["project_revision"], "payload": payload})


def final_payload(preview):
    return {"snapshot_id": preview["id"], "snapshot_digest": preview["request_digest"],
            "command_id": str(uuid4()), "command_context": preview["command_context"]}


async def test_clear_request_waits_across_refresh_and_recompile_then_exact_edits_execute_once(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做钢笔电商主图，极简冷白背景，无文字")
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["stage"] == "S07" and view["status"] == "needs_input", view
        preview = view["prompt_preview"]
        assert preview["can_confirm"]
        assert {"confirm_prompt", "edit_prepared_prompt"} <= {a["id"] for a in view["available_actions"]}
        assert len(visual.submit_calls) == 0
        async with app.state.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(GenerationJob)) == 0
            assert await session.scalar(select(func.count()).select_from(FinalAuthorization)) == 0
        calls = len(llm.role_calls_received)
        for _ in range(2):
            refreshed = await _view(client, project["id"])
            assert refreshed["prompt_preview"]["id"] == preview["id"]
        compiled = await action(client, project["id"], view, "compile_prompt", {})
        assert compiled.status_code in {200, 409}
        assert len(visual.submit_calls) == 0
        positive, negative = "钢笔侧面特写，明亮白色背景，精细金属纹理", "不要文字，不要黑色背景"
        edited = await action(client, project["id"], view, "edit_prepared_prompt", {
            "snapshot_id": preview["id"], "snapshot_digest": preview["request_digest"],
            "positive_prompt": positive, "negative_prompt": negative})
        assert edited.status_code == 200, edited.text
        current = edited.json()
        newer = current["prompt_preview"]
        assert newer["id"] != preview["id"]
        assert newer["request"]["positive_prompt"] == positive
        assert newer["request"]["negative_prompt"] == negative
        assert len(visual.submit_calls) == 0 and len(llm.role_calls_received) == calls
        assert (await _view(client, project["id"]))["prompt_preview"] == newer
        stale = await action(client, project["id"], current, "confirm_prompt", final_payload(preview))
        assert stale.status_code == 409
        payload = final_payload(newer)
        confirmed = await action(client, project["id"], current, "confirm_prompt", payload)
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        delivered = await _view(client, project["id"])
        assert delivered["status"] == "completed"
        assert delivered["versions"][0]["prompt"]["positive_prompt"] == positive
        assert delivered["versions"][0]["prompt"]["negative_prompt"] == negative
        async with app.state.session_factory() as session:
            snapshots = (await session.scalars(select(PreparedPrompt).order_by(PreparedPrompt.created_at))).all()
            assert {s.status for s in snapshots} == {"invalidated", "authorized"}
            auth = await session.scalar(select(FinalAuthorization))
            assert auth.snapshot_id == newer["id"]
        from app.final_confirmation import execution_request
        request = await execution_request(app, auth.generation_id)
        assert request["positive_prompt"] == positive and request["negative_prompt"] == negative
        replay = await action(client, project["id"], current, "confirm_prompt", payload)
        assert replay.status_code == 200, replay.text
        assert len(visual.submit_calls) == 1


async def test_bare_subject_is_guided_even_when_model_claims_ready(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "帮我做一张钢笔宣传图")
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert view["conversation_question"] is not None
        assert view["prompt_preview"] is None
        assert len(visual.submit_calls) == 0


async def test_delegation_prepares_but_does_not_confirm(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做钢笔海报，你来定，直接做")
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["prompt_preview"]["can_confirm"] is True
        assert view["status"] == "needs_input"
        assert len(visual.submit_calls) == 0


async def test_invalid_edit_and_paused_confirmation_do_not_submit(full_settings):
    from tests.test_collaborative_revisions import send

    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "钢笔电商主图，冷白背景，居中构图，无文字")
        await _drain(app)
        view = await _view(client, project["id"])
        preview = view["prompt_preview"]
        invalid = await action(client, project["id"], view, "edit_prepared_prompt", {
            "snapshot_id": preview["id"], "snapshot_digest": preview["request_digest"],
            "positive_prompt": "   ", "negative_prompt": ""})
        assert invalid.status_code == 409
        assert (await _view(client, project["id"]))["prompt_preview"]["id"] == preview["id"]
        paused = await send(client, project["id"], "先别生成，我再想想")
        assert paused.status_code == 200, paused.text
        current = await _view(client, project["id"])
        denied = await action(client, project["id"], current, "confirm_prompt", final_payload(preview))
        assert denied.status_code == 409
        assert len(visual.submit_calls) == 0


@pytest.mark.parametrize("positive", ["## 正向提示词", "```markdown\n\n```"])
async def test_edit_cannot_save_a_prompt_without_body(full_settings, positive):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "钢笔电商主图，冷白背景，居中构图，无文字")
        await _drain(app)
        view = await _view(client, project["id"])
        preview = view["prompt_preview"]
        calls = len(llm.role_calls_received)
        denied = await action(client, project["id"], view, "edit_prepared_prompt", {
            "snapshot_id": preview["id"], "snapshot_digest": preview["request_digest"],
            "positive_prompt": positive, "negative_prompt": ""})
        assert denied.status_code == 409, denied.text
        assert denied.json()["error"]["code"] == "PROMPT_EMPTY", denied.text
        assert (await _view(client, project["id"]))["prompt_preview"]["id"] == preview["id"]
        assert visual.submit_calls == [] and len(llm.role_calls_received) == calls
