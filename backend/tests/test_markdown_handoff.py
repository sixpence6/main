import json

import httpx
import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome, RoleRequest, RoleResponse
from app.adapters.openai_chat import OpenAIChatAdapter
from app.workflow.tables import Artifact
from tests.support.t02_prompt import final_click
from tests.test_collaborative_revisions import send, settings_for
from tests.test_openai_chat_adapter import _completion, _role
from tests.test_t65_generation_cycle import _drain
from tests.test_v2_t05_requirements import _create_project, _environment, _messages, _start, _view
from tests.test_v2_t40_prompt_review import png_bytes

REQ = "# 商品主体\n钢笔\n\n# 用途\n电商主图\n\n# 已确定方向\n浅色背景，细节清楚，不添加文字。"
VISUAL = "# 画面方案\n钢笔居中，浅色台面，柔和侧光。\n\n## 构图\n产品完整呈现。\n" + "细节层次。" * 700 + "\n末尾交接：保持笔夹原形，不加装饰。"
PROMPT = "# 正向提示词\n钢笔电商主图，浅色台面，柔和侧光，保持笔夹原形。\n\n# 反向提示词\n不要文字，不要多余装饰。"


class MarkdownScript:
    def __init__(self, *, question=False):
        self.question = question
        self.calls = []

    def responder(self, request):
        self.calls.append(request)
        role = request.payload["task"]["role"]
        doc = {"requirements_assistant": REQ, "visual_planner": VISUAL,
               "generation_engineer": PROMPT, "reference_analyst": "# 观察\n图中钢笔主体清楚，笔夹可见；未发现可辨认品牌文字。"}[role]
        if role == "requirements_assistant" and self.question:
            self.question = False
            doc = "钢笔主体已知。\n\n这张图更喜欢哪种画面方向？\nA. 浅色桌面，细节清楚\nB. 深色背景，产品突出\nC. 你帮我选"
        return RoleResponse(payload={}, model_fingerprint="md-stub", text=doc)


async def test_adapter_preserves_markdown_and_does_not_request_json():
    seen = []
    doc = "这是一段自由写作的方案。\n\n- 浅色背景\n- 保留原形\n无需任何固定字段。"

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(doc))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAIChatAdapter(_role(), client).complete(RoleRequest(
            system_prompt="用 Markdown 交接", payload={"markdown_context": "# 上游原文\n保持颜色"}, output_format="markdown"))
    assert result.text == doc and result.payload == {}
    assert "response_format" not in seen[0]
    assert seen[0]["messages"][1]["content"] == "# 上游原文\n保持颜色"


async def test_full_handoff_is_original_markdown_and_waits_for_editable_prompt_confirmation(full_settings):
    script = MarkdownScript()
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做钢笔电商主图，浅色背景，不需要文字")
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["stage"] == "S07" and view["status"] == "needs_input", view
        assert len(visual.submit_calls) == 0
        assert [r.payload["task"]["role"] for r in script.calls] == ["requirements_assistant", "visual_planner", "generation_engineer"]
        assert all(r.output_format == "markdown" and "JSON Schema" not in r.system_prompt for r in script.calls)
        assert REQ in script.calls[1].payload["markdown_context"]
        assert REQ in script.calls[2].payload["markdown_context"]
        assert VISUAL in script.calls[2].payload["markdown_context"]
        preview = view["prompt_preview"]
        assert preview["request"]["positive_prompt"] == "钢笔电商主图，浅色台面，柔和侧光，保持笔夹原形。"
        assert preview["request"]["negative_prompt"] == "不要文字，不要多余装饰。"
        async with app.state.session_factory() as session:
            docs = (await session.scalars(select(Artifact).where(Artifact.run_id == view["run_id"], Artifact.kind.in_(["requirements_markdown", "visual_markdown", "prompt_markdown"])))).all()
            assert {json.loads(d.payload_json)["content"] for d in docs} == {REQ, VISUAL, PROMPT}
        await _view(client, project["id"])
        assert len(script.calls) == 3
        confirmed = await final_click(client, {"project_id": project["id"], "workflow": view})
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        assert (await _view(client, project["id"]))["status"] == "completed"


async def test_markdown_question_and_free_answer_remain_in_downstream_context(full_settings):
    script = MarkdownScript(question=True)
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "帮我做钢笔宣传图")
        assert view["stage"] == "S03" and view["status"] == "needs_input", view
        assert view["conversation_question"]["options"][0]["id"] == "A"
        result = await send(client, project["id"], "A，但别太暗，不需要文字")
        assert result.status_code == 200, result.text
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["prompt_preview"] is not None, {k: view[k] for k in ["stage", "status", "issue", "available_actions"]}
        context = script.calls[-1].payload["markdown_context"]
        assert "A，但别太暗，不需要文字" in context
        assert len(visual.submit_calls) == 0
        assert any("A，但别太暗" in m["content"] for m in await _messages(client, project["id"]))


async def test_markdown_discussion_does_not_prepare_or_generate(full_settings):
    async with _environment(settings_for(full_settings), MarkdownScript()) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "先讨论钢笔主图怎么做，先别生成")
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["status"] == "needs_input" and view["prompt_preview"] is None
        assert len(llm.role_calls_received) == 1 and len(visual.submit_calls) == 0

async def test_markdown_prompt_edits_are_exact_and_old_confirmation_is_rejected(full_settings):
    from tests.test_editable_prompt_confirmation import action, final_payload

    async with _environment(settings_for(full_settings), MarkdownScript()) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做钢笔电商主图，浅色背景，不需要文字")
        await _drain(app)
        view = await _view(client, project["id"])
        original = view["prompt_preview"]
        positive, negative = "钢笔侧面特写，米白背景，保留笔夹原形。", "禁止文字和多余装饰。"
        edited = await action(client, project["id"], view, "edit_prepared_prompt", {
            "snapshot_id": original["id"], "snapshot_digest": original["request_digest"],
            "positive_prompt": positive, "negative_prompt": negative})
        assert edited.status_code == 200, edited.text
        current = edited.json()
        preview = current["prompt_preview"]
        assert preview["id"] != original["id"]
        assert preview["request"]["positive_prompt"] == positive
        assert preview["request"]["negative_prompt"] == negative
        assert len(visual.submit_calls) == 0 and len(llm.role_calls_received) == 3
        stale = await action(client, project["id"], current, "confirm_prompt", final_payload(original))
        assert stale.status_code == 409
        payload = final_payload(preview)
        response = await action(client, project["id"], current, "confirm_prompt", payload)
        assert response.status_code == 200, response.text
        await _drain(app)
        delivered = await _view(client, project["id"])
        assert delivered["status"] == "completed"
        assert delivered["versions"][0]["prompt"]["positive_prompt"] == positive
        assert delivered["versions"][0]["prompt"]["negative_prompt"] == negative
        replay = await action(client, project["id"], current, "confirm_prompt", payload)
        assert replay.status_code == 200, replay.text
        assert len(visual.submit_calls) == 1


@pytest.mark.parametrize("failed_role", ["requirements_assistant", "visual_planner", "generation_engineer"])
@pytest.mark.parametrize("original_format", ["json", "markdown"])
async def test_failed_conversation_resumes_with_markdown_without_auto_generation(full_settings, failed_role, original_format):
    from app.adapters.base import ProviderError
    from tests.test_role_chain_consolidation import ConsolidatedScript

    base = MarkdownScript() if original_format == "markdown" else ConsolidatedScript()

    class FailingScript:
        def responder(self, request):
            if request.payload["task"]["role"] == failed_role:
                raise ProviderError("controlled failure", error_code="LLM_TIMEOUT", evidence="uncertain")
            return base.responder(request)

    settings = settings_for(full_settings).model_copy(update={"workflow_handoff_format": original_format})
    async with _environment(settings, FailingScript()) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)
        project = await _create_project(client)
        initial = await client.post(f"/api/projects/{project['id']}/workflow/actions/submit_request", json={
            "expected_revision": 0, "text": "帮我做钢笔电商主图，浅色背景，不需要文字"})
        assert initial.status_code in (200, 503, 504), initial.text
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["status"] == "failed", view
        call_count = len(llm.role_calls_received)
        await _view(client, project["id"])
        assert len(llm.role_calls_received) == call_count and len(visual.submit_calls) == 0
        app.state.settings.workflow_handoff_format = "markdown"
        resumed = MarkdownScript()
        llm.role_responder = resumed.responder
        response = await client.post(f"/api/projects/{project['id']}/workflow/actions/continue_workflow", json={
            "expected_revision": view["project_revision"]})
        assert response.status_code == 200, response.text
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["stage"] == "S07" and view["status"] == "needs_input", view
        assert view["prompt_preview"] is not None and len(visual.submit_calls) == 0
        assert resumed.calls and all(r.output_format == "markdown" for r in resumed.calls)
        assert "帮我做钢笔电商主图，浅色背景，不需要文字" in resumed.calls[-1].payload["markdown_context"]
        confirmed = await final_click(client, {"project_id": project["id"], "workflow": view})
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        assert (await _view(client, project["id"]))["status"] == "completed"
        assert len(visual.submit_calls) == 1

async def test_answered_direction_survives_visual_failure_and_manual_resume(full_settings):
    from app.adapters.base import ProviderError

    class AfterAnswer(MarkdownScript):
        failed = False

        def responder(self, request):
            if request.payload["task"]["role"] == "visual_planner" and not self.failed:
                self.failed = True
                raise ProviderError("controlled timeout", error_code="LLM_TIMEOUT", evidence="uncertain")
            return super().responder(request)

    script = AfterAnswer(question=True)
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "帮我做钢笔宣传图")
        assert view["conversation_question"] is not None
        answer = "A，浅色桌面，保持笔夹原形，不需要文字"
        response = await send(client, project["id"], answer)
        assert response.status_code in (200, 503, 504), response.text
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["status"] == "failed" and view["stage"] == "S04", view
        calls = len(script.calls)
        response = await client.post(f"/api/projects/{project['id']}/workflow/actions/continue_workflow", json={
            "expected_revision": view["project_revision"]})
        assert response.status_code == 200, response.text
        await _drain(app)
        view = await _view(client, project["id"])
        assert view["stage"] == "S07" and view["prompt_preview"] is not None, view
        assert [r.payload["task"]["role"] for r in script.calls[calls:]] == ["visual_planner", "generation_engineer"]
        assert all(answer in r.payload["markdown_context"] for r in script.calls[calls:])
        assert any(m["content"] == answer for m in await _messages(client, project["id"]))
        assert len(visual.submit_calls) == 0


def test_question_heading_is_detected_without_a_fixed_markdown_template():
    from app.workflow.markdown_handoff import questions

    question = questions("## 更喜欢哪种画面方向？\n\nA. 浅色桌面\nB. 深色背景\nC. 你帮我选")
    assert len(question) == 1
    assert question[0]["text"] == "更喜欢哪种画面方向？"
    assert [option["id"] for option in question[0]["options"]] == ["A", "B", "C"]
