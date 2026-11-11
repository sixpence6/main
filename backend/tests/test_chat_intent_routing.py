"""Chat intent regressions: authenticated API, temporary SQLite, scripted models."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.acceptance import GenerationIntent, GenerationUsage
from app.adapters.base import BriefData, OrganizeResult, ProviderOutcome
from app.models import Project
from app.workflow.alignment import intent
from app.workflow.tables import Artifact, GenerationJob, WorkflowRun
from tests.support.t02_prompt import final_click
from tests.test_collaborative_revisions import answer, send, settings_for
from tests.test_conversation_alignment import first
from tests.test_role_chain_consolidation import ConsolidatedScript
from tests.test_t65_generation_cycle import _drain
from tests.test_v2_t05_requirements import _create_project, _environment, _start, _view
from tests.test_v2_t40_prompt_review import png_bytes

BRIEF = "米色托特包电商主图，极简冷白背景，不加文字"
CHAT = [
    "你好",
    "谢谢",
    "你能帮我做什么",
    "我们先讨论一下背景风格",
    "我觉得冷白背景比较适合钢笔主图",
    "我喜欢这个背景",
    "我很喜欢这个文案",
]
REQUESTS = [
    BRIEF,
    "帮我做一张" + BRIEF,
    "你好，帮我做一张钢笔宣传图，极简冷白背景",
    "制作钢笔主图，极简冷白背景，文案为你好",
    pytest.param("你可以帮我做一张钢笔主图，极简冷白背景，无文字", id="polite-request"),
    pytest.param("制作钢笔主图，极简冷白背景，文案为为什么选择我们", id="question-word-copy"),
]


@pytest.mark.parametrize("text", CHAT + ["你来定", "先别生成钢笔主图"])
def test_fresh_chat_does_not_authorize_production(text):
    assert intent({}, text, "message")["status"] != "requested"


@pytest.mark.parametrize("text", REQUESTS)
def test_request_and_product_brief_authorize_preparation(text):
    assert intent({}, text, "message")["status"] == "requested"


async def snapshot(app, client, project_id):
    view = await _view(client, project_id)
    fields = (
        "run_id",
        "stage",
        "status",
        "requirement_revision",
        "intent_route",
        "intent_action",
        "conversation_question",
        "prompt_preview",
        "generation_job",
        "execution_snapshot",
        "delivery",
        "versions",
        "usage",
    )
    result = {key: view.get(key) for key in fields}
    # The rolling window display moves with wall-clock time; persisted usage is below.
    if result["usage"]:
        result["usage"] = {
            key: value
            for key, value in result["usage"].items()
            if key not in {"window_started_at", "window_expires_at"}
        }
    async with app.state.session_factory() as session:
        project = await session.get(Project, project_id)
        result["project"] = {
            column.name: getattr(project, column.name)
            for column in Project.__table__.columns
            if column.name not in {"title", "updated_at", "created_at"}
        }
        for model in (WorkflowRun, Artifact, GenerationJob, GenerationIntent, GenerationUsage):
            table = model.__table__
            rows = (
                (await session.execute(select(table).where(table.c.project_id == project_id)))
                .mappings()
                .all()
            )
            result[table.name] = sorted(
                [{key: value for key, value in row.items() if key != "updated_at"} for row in rows],
                key=str,
            )
    return result


def production_calls(llm):
    return [
        r.payload["task"]["role"]
        for r in llm.role_calls_received
        if r.payload["task"]["role"] in {"visual_planner", "generation_engineer"}
    ]


def image_result():
    return ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)


async def confirm(client, project_id, view):
    response = await final_click(
        client, {"project_id": project_id, "workflow": view}, command_id=str(uuid4())
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("phase", ["new", "clarification", "prompt", "generating", "completed"])
async def test_chat_preserves_lifecycle_even_when_model_returns_wrong_ready_brief(
    full_settings, phase
):
    # Default adapter deliberately claims image_request/ready with a filled brief.
    # The application guard must win; a scripted conversation answer cannot hide a bug.
    def wrong_ready(request):
        return answer(request, product_description="钢笔", style_keywords=["极简", "冷白背景"])

    script = ConsolidatedScript(answers=[first, wrong_ready] if phase == "clarification" else None)
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        llm.responder = lambda request: OrganizeResult(
            outcome="review",
            assistant_message="已整理好商品图需求。",
            brief=BriefData(
                product_description="凭空出现的托特包",
                purpose="电商主图",
                prompt="托特包，冷白背景",
            ),
        )
        gate = asyncio.Event()
        visual.submit_outcome = image_result()
        try:
            if phase != "new":
                await _start(
                    client,
                    project_id,
                    "帮我做钢笔宣传图，送礼用" if phase == "clarification" else BRIEF,
                )
                await _drain(app)
            if phase in {"generating", "completed"}:
                if phase == "generating":
                    visual.gate = gate
                await confirm(client, project_id, await _view(client, project_id))
                if phase == "completed":
                    await _drain(app)
                    assert (await _view(client, project_id))["status"] == "completed"
                else:
                    for _ in range(1000):
                        if visual.submit_calls:
                            break
                        await asyncio.sleep(0.01)
                    assert len(visual.submit_calls) == 1, await _view(client, project_id)
            view = await _view(client, project_id)
            if phase == "clarification":
                assert view["conversation_question"]
            if phase == "prompt":
                assert view["prompt_preview"]["can_confirm"]
            before = await snapshot(app, client, project_id)
            calls, submits = production_calls(llm), len(visual.submit_calls)
            for text in CHAT:
                # This concrete preference answers first()'s pending style question.
                if phase == "clarification" and text == "我觉得冷白背景比较适合钢笔主图":
                    continue
                response = await send(client, project_id, text)
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["user_message"]["content"] == text
                assert body["assistant_message"]["content"].strip()
                assert body["assistant_message"]["turn_kind"] == "conversation", text
                assert production_calls(llm) == calls
                assert len(visual.submit_calls) == submits
                assert await snapshot(app, client, project_id) == before, text
            if phase == "prompt":
                # Exact preview survived all chat turns; the original snapshot remains usable.
                await confirm(client, project_id, view)
                await _drain(app)
                assert len(visual.submit_calls) == 1
        finally:
            gate.set()
            await _drain(app)


@pytest.mark.parametrize("text", REQUESTS)
async def test_first_message_prepares_prompt_and_only_confirm_submits(full_settings, text):
    copy = text.partition("文案为")[2]
    script = ConsolidatedScript(
        answers=[
            lambda request: answer(
                request,
                kind="requirements_change",
                texts=[copy],
                style_keywords=["极简", "冷白背景"],
            )
        ]
        if copy
        else None
    )
    async with _environment(settings_for(full_settings), script) as (
        app,
        client,
        llm,
        visual,
    ):
        project_id = (await _create_project(client))["id"]
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "workflow"
        await _drain(app)
        view = await _view(client, project_id)
        assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"], view
        assert {"visual_planner", "generation_engineer"} <= set(production_calls(llm))
        if copy:
            brief = next(
                item["payload"] for item in view["artifacts"] if item["kind"] == "brief_draft"
            )
            assert brief["texts"] == [copy]
        assert visual.submit_calls == []
        visual.submit_outcome = image_result()
        await confirm(client, project_id, view)
        await _drain(app)
        assert len(visual.submit_calls) == 1


@pytest.mark.parametrize("reply", ["A，但别太暗", "你来定"])
async def test_aesthetic_question_accepts_bound_answer_and_delegation(full_settings, reply):
    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[first])) as (
        app,
        client,
        llm,
        visual,
    ):
        project_id = (await _create_project(client))["id"]
        view = await _start(client, project_id, "帮我做钢笔宣传图，送礼用")
        question = view["conversation_question"]
        body = {
            "content": reply,
            "expected_revision": view["project_revision"],
            "request_id": str(uuid4()),
        }
        if reply.startswith("A"):
            body["clarification_answer"] = {
                **{
                    key: question[key]
                    for key in ("question_id", "message_id", "requirement_revision")
                },
                "option_id": "A",
            }
        response = await client.post(f"/api/projects/{project_id}/messages", json=body)
        assert response.status_code == 200, response.text
        await _drain(app)
        view = await _view(client, project_id)
        assert view["conversation_question"] is None
        assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"]
        if reply.startswith("A"):
            brief = next(a["payload"] for a in view["artifacts"] if a["kind"] == "brief_draft")
            assert "深色背景" in str(brief) and "别太暗" in str(brief)
        assert visual.submit_calls == []


@pytest.mark.parametrize("reply", ["钢笔", "你来定"])
async def test_missing_product_requires_real_field_answer(full_settings, reply):
    question = {
        "id": "subject",
        "field": "product_description",
        "text": "具体是什么商品？",
        "options": [],
    }

    def missing(request):
        return answer(request, status="needs_input", questions=[question], product_description=None)

    def filled(request):
        return answer(request, product_description="钢笔", style_keywords=["极简", "冷白背景"])

    async with _environment(
        settings_for(full_settings), ConsolidatedScript(answers=[missing, filled])
    ) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        await _start(client, project_id, "给我的商品做一张宣传图，极简冷白背景")
        response = await send(client, project_id, reply)
        assert response.status_code == 200, response.text
        await _drain(app)
        view = await _view(client, project_id)
        if reply == "你来定":
            assert view["conversation_question"]["field"] == "product_description"
            assert view.get("prompt_preview") is None
            assert production_calls(llm) == []
        else:
            assert view["conversation_question"] is None
            assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"]
        assert visual.submit_calls == []


async def test_explicit_pause_on_first_message_never_prepares_images(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        project_id = (await _create_project(client))["id"]
        response = await send(client, project_id, "先别生成，讨论一下钢笔主图的背景")
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "conversation"
        await _drain(app)
        assert (await _view(client, project_id)).get("prompt_preview") is None
        assert production_calls(llm) == []
        assert visual.submit_calls == []


def copy_question_script(kind):
    question = {
        "id": "copy",
        "field": "texts",
        "options": [],
        "text": "请提供要放到图上的文案原文；不需要文字也可以直接告诉我。",
    }

    def pending(request):
        return answer(request, status="needs_input", questions=[question], texts=[])

    def ready(request):
        return answer(request, kind=kind, texts=[], style_keywords=["极简", "冷白背景"])

    return ConsolidatedScript(answers=[pending, ready])


@pytest.mark.parametrize("kind", ["clarification", "requirements_change"])
async def test_no_copy_answer_continues_authorized_request_regardless_of_model_kind(
    full_settings, kind
):
    async with _environment(settings_for(full_settings), copy_question_script(kind)) as (
        app,
        client,
        llm,
        visual,
    ):
        project_id = (await _create_project(client))["id"]
        view = await _start(
            client, project_id, "制作米色托特包电商主图，极简冷白背景，文案稍后确定"
        )
        assert view["conversation_question"]["field"] == "texts"
        assert view["conversation_question"]["options"] == []
        response = await send(client, project_id, "不用")
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "workflow"
        await _drain(app)
        view = await _view(client, project_id)
        brief = next(item["payload"] for item in view["artifacts"] if item["kind"] == "brief_draft")
        assert brief["texts"] == []
        assert view["conversation_question"] is None
        assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"]
        assert {"visual_planner", "generation_engineer"} <= set(production_calls(llm))
        assert visual.submit_calls == []


async def test_greeting_does_not_answer_pending_free_text_copy_question(full_settings):
    async with _environment(
        settings_for(full_settings), copy_question_script("requirements_change")
    ) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        view = await _start(
            client, project_id, "制作米色托特包电商主图，极简冷白背景，文案稍后确定"
        )
        assert view["conversation_question"]["field"] == "texts"
        assert view["conversation_question"]["options"] == []
        before = await snapshot(app, client, project_id)
        calls = production_calls(llm)
        response = await send(client, project_id, "你好")
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "conversation"
        assert response.json()["assistant_message"]["content"].strip()
        assert await snapshot(app, client, project_id) == before
        assert production_calls(llm) == calls
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "phase,text",
    [
        ("new", "我觉得冷白背景比较适合钢笔主图"),
        ("prompt", "我喜欢这个背景"),
    ],
)
async def test_product_opinion_does_not_authorize_or_revise_production(full_settings, phase, text):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        project_id = (await _create_project(client))["id"]
        # Also make the old no-run organize path offer a complete invented brief.
        llm.responder = lambda request: OrganizeResult(
            outcome="review",
            assistant_message="可以开始准备主图。",
            brief=BriefData(
                product_description="钢笔", purpose="电商主图", prompt="钢笔，冷白背景"
            ),
        )
        if phase == "prompt":
            await _start(client, project_id, BRIEF)
            await _drain(app)
            assert (await _view(client, project_id))["prompt_preview"]["can_confirm"]
        before = await snapshot(app, client, project_id)
        calls = production_calls(llm)
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "conversation"
        assert await snapshot(app, client, project_id) == before
        assert production_calls(llm) == calls
        assert visual.submit_calls == []


async def test_active_copy_praise_is_not_pending_feedback_when_model_says_conversation(
    full_settings,
):
    def conversation(request):
        response = answer(request, kind="conversation")
        response["data"].update(brief=None, reply="很高兴你喜欢这个文案。")
        return response

    script = ConsolidatedScript()
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        await _start(client, project_id, BRIEF)
        await _drain(app)
        script.answers = [conversation]
        gate = asyncio.Event()
        visual.gate = gate
        visual.submit_outcome = image_result()
        try:
            await confirm(client, project_id, await _view(client, project_id))
            for _ in range(1000):
                if visual.submit_calls:
                    break
                await asyncio.sleep(0.01)
            assert len(visual.submit_calls) == 1
            before = await snapshot(app, client, project_id)
            calls = production_calls(llm)
            response = await send(client, project_id, "我很喜欢这个文案")
            assert response.status_code == 200, response.text
            assert response.json()["assistant_message"]["turn_kind"] == "conversation"
            after = await snapshot(app, client, project_id)
            before_state = json.loads(before["workflow_runs"][0]["requirements_state_json"])
            after_state = json.loads(after["workflow_runs"][0]["requirements_state_json"])
            assert after_state.get("pending_feedback", []) == before_state.get(
                "pending_feedback", []
            )
            assert after == before
            assert production_calls(llm) == calls
            assert len(visual.submit_calls) == 1
        finally:
            gate.set()
            await _drain(app)


@pytest.mark.parametrize("text", ["我喜欢冷白背景", "我觉得冷白背景比较适合钢笔主图"])
async def test_concrete_preference_answers_current_aesthetic_question(full_settings, text):
    def clarified(request):
        return answer(
            request,
            kind="clarification",
            product_description="钢笔",
            purpose="商务送礼",
            style_keywords=["冷白背景"],
        )

    async with _environment(
        settings_for(full_settings), ConsolidatedScript(answers=[first, clarified])
    ) as (app, client, llm, visual):
        project_id = (await _create_project(client))["id"]
        before = await _start(client, project_id, "帮我做钢笔宣传图，送礼用")
        assert before["conversation_question"]["field"] == "style_keywords"
        assert before["conversation_question"]["options"]
        response = await send(client, project_id, text)
        assert response.status_code == 200, response.text
        assert response.json()["assistant_message"]["turn_kind"] == "workflow", text
        await _drain(app)
        view = await _view(client, project_id)
        brief = next(item["payload"] for item in view["artifacts"] if item["kind"] == "brief_draft")
        assert any("冷白背景" in value for value in brief["style_keywords"])
        assert view["requirement_revision"] > before["requirement_revision"]
        assert view["conversation_question"] is None
        assert view["prompt_preview"] and view["prompt_preview"]["can_confirm"]
        assert {"visual_planner", "generation_engineer"} <= set(production_calls(llm))
        assert visual.submit_calls == []
        visual.submit_outcome = image_result()
        await confirm(client, project_id, view)
        await _drain(app)
        assert len(visual.submit_calls) == 1
