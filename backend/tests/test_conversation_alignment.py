from tests.support.prompt_confirmation import finish_with_prompt_confirmation

"""P0 conversation alignment: real API, persisted questions and submission counts."""

from uuid import uuid4

import pytest

from app.adapters.base import ProviderOutcome
from app.workflow.alignment import choose_questions, intent
from app.workflow.requirements import parse_answers
from tests.test_collaborative_revisions import answer, send, settings_for
from tests.test_role_chain_consolidation import ConsolidatedScript
from tests.test_v2_t05_requirements import _create_project, _environment, _messages, _start, _view
from tests.test_v2_t40_prompt_review import png_bytes

QUESTION = {
    "id": "direction",
    "field": "style_keywords",
    "text": "你更喜欢哪种背景？",
    "options": [
        {"id": "A", "label": "深色背景，产品突出", "value": "深色背景", "requires_detail": False},
        {"id": "B", "label": "浅色桌面，自然精致", "value": "浅色桌面", "requires_detail": False},
        {
            "id": "C",
            "label": "你帮我选",
            "value": "浅色桌面",
            "requires_detail": False,
            "delegates": True,
        },
    ],
}


def first(request):
    return answer(
        request,
        status="needs_input",
        questions=[QUESTION],
        product_description="钢笔",
        purpose="商务送礼",
        style_keywords=[],
        texts=[],
    )


async def test_vague_request_waits_even_if_model_returns_ready(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做一张钢笔宣传图，高级一点")
        view = await _view(client, project["id"])
        question = view["conversation_question"]
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert question["field"] == "purpose" and len(question["options"]) == 3
        assert (await _view(client, project["id"]))["conversation_question"] == question
        assert visual.submit_calls == []
        assert (await _messages(client, project["id"]))[-1]["id"] == question["message_id"]


@pytest.mark.parametrize(
    "reply",
    ["A，但别太暗，不要文字", "A，但别太暗，不需要文字", "送礼用，背景别太暗", "你来定", "C"],
)
async def test_answers_accumulate_and_continue_original_request(full_settings, reply):
    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[first])) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做一张钢笔宣传图，送礼用")
        response = await send(client, project["id"], reply)
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert len(visual.submit_calls) == 1 and view["status"] == "completed", view
        brief = next(a["payload"] for a in view["artifacts"] if a["kind"] == "brief_draft")
        assert brief["product_description"] == "钢笔" and brief["purpose"] == "商务送礼"
        if "别太暗" in reply:
            assert "别太暗" in str(brief["style_keywords"])
        assert view["conversation_question"] is None


async def test_bound_option_is_idempotent_and_old_option_is_rejected(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[first])) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        view = await _start(client, project["id"], "帮我做钢笔宣传图")
        question = view["conversation_question"]
        binding = {
            key: question[key] for key in ("question_id", "message_id", "requirement_revision")
        }
        binding["option_id"] = "A"
        body = {
            "content": "A",
            "expected_revision": view["project_revision"],
            "request_id": str(uuid4()),
            "clarification_answer": binding,
        }
        path = f"/api/projects/{project['id']}/messages"
        response = await client.post(path, json=body)
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assert (await client.post(path, json=body)).status_code == 200
        body["request_id"] = str(uuid4())
        body["expected_revision"] = (await _view(client, project["id"]))["project_revision"]
        stale = await client.post(path, json=body)
        assert stale.status_code == 409 and "QUESTION_EXPIRED" in stale.text
        assert len(visual.submit_calls) == 1


@pytest.mark.parametrize(
    "text", ["先讨论一下钢笔宣传图，先别生成", "这两种钢笔图有什么区别", "先别生成，暂停制作"]
)
async def test_discussion_never_submits_even_if_model_says_ready(full_settings, text):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        project = await _create_project(client)
        await _start(client, project["id"], text)
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert visual.submit_calls == []


async def test_pause_then_explicit_request_can_resume(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "先讨论钢笔主图，先别生成")
        assert visual.submit_calls == []
        response = await send(client, project["id"], "现在按这个方向直接做")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assert len(visual.submit_calls) == 1


def test_budget_and_delegation_never_fill_missing_product_facts():
    hard = {"id": "copy", "field": "texts", "text": "请提供文字原文", "options": []}
    for state, text in [({"exploration_rounds": 3}, "A"), ({}, "你来定")]:
        questions, _ = choose_questions(
            state, [dict(QUESTION), dict(hard)], {}, text, set(), "run", 2
        )
        assert len(questions) == 1 and questions[0]["field"] == "texts"
    assert (
        choose_questions({"exploration_rounds": 3}, [dict(QUESTION)], {}, "A", set(), "run", 2)[0]
        == []
    )


def test_mixed_answer_and_orphan_letter():
    parsed = parse_answers([QUESTION], "A，但别太暗")
    assert parsed[0]["answer"] == "深色背景" and "别太暗" in parsed[0]["raw_text"]
    assert parse_answers([], "A") == []
    assert (
        intent({"generation_intent": {"status": "requested"}}, "先别做", "m")["status"] == "paused"
    )


@pytest.mark.parametrize("text", ["我也不知道，你帮我选吧", "不好看", "太乱了", "不够高级"])
def test_advice_and_feedback_do_not_create_generation_intent(text):
    assert intent({}, text, "m")["status"] != "requested"


def test_clear_direction_skips_model_optional_questions():
    questions, _ = choose_questions(
        {}, [dict(QUESTION)], {}, "电商主图，极简冷白背景，不加文字", set(), "run", 1
    )
    assert questions == []


async def test_unknown_remote_result_records_feedback_without_resubmission(full_settings):
    import json

    from app.workflow.tables import WorkflowRun

    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="unknown", error_code="REMOTE_UNKNOWN", error_message="结果待查询"
        )
        project = await _create_project(client)
        await _start(client, project["id"], "制作米色托特包电商主图，极简冷白背景，不加文字")
        await finish_with_prompt_confirmation(app, client)
        before = await _view(client, project["id"])
        calls = len(llm.role_calls_received)
        response = await send(client, project["id"], "背景再干净一点，先别重新生成")
        assert response.status_code == 200, response.text
        after = await _view(client, project["id"])
        assert len(visual.submit_calls) == 1 and len(llm.role_calls_received) == calls
        assert after["execution_snapshot"] == before["execution_snapshot"]
        async with app.state.session_factory() as session:
            run = await session.get(WorkflowRun, before["run_id"])
            state = json.loads(run.requirements_state_json)
        assert state["pending_feedback"][-1]["text"] == "背景再干净一点，先别重新生成"


async def test_delegation_cannot_invent_missing_subject(full_settings):
    question = {
        "id": "subject",
        "field": "product_description",
        "text": "具体是什么商品？",
        "options": [],
    }

    def missing(request):
        return answer(request, status="needs_input", questions=[question], product_description=None)

    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[missing])) as (
        app,
        client,
        llm,
        visual,
    ):
        project = await _create_project(client)
        await _start(client, project["id"], "给我的商品做一张宣传图")
        response = await send(client, project["id"], "不知道，你来定，直接做")
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        assert view["conversation_question"]["field"] == "product_description"
        assert visual.submit_calls == []


async def test_conflict_is_not_defaulted_after_delegation(full_settings):
    conflict = {**QUESTION, "purpose": "conflict", "options": []}

    def ask(request):
        return answer(request, status="needs_input", questions=[conflict])

    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[ask])) as (
        app,
        client,
        llm,
        visual,
    ):
        project = await _create_project(client)
        await _start(client, project["id"], "给商品做宣传图，必须纯白背景，同时必须纯黑背景")
        response = await send(client, project["id"], "你来定，直接做")
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        assert view["status"] == "needs_input" and view["conversation_question"]
        assert visual.submit_calls == []


async def test_conversation_kind_with_a_complete_brief_remains_discussion(full_settings):
    def discuss(request):
        return answer(request, kind="conversation", product_description="钢笔", purpose="宣传图")

    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[discuss])) as (
        app,
        client,
        llm,
        visual,
    ):
        project = await _create_project(client)
        await _start(client, project["id"], "我想了解钢笔宣传图")
        await finish_with_prompt_confirmation(app, client)
        assert visual.submit_calls == []
        assert (await _view(client, project["id"]))["status"] == "needs_input"


async def test_orphan_letter_after_delivery_does_not_create_a_task(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "米色托特包，冷白背景，不需要文字")
        await finish_with_prompt_confirmation(app, client)
        before = await _view(client, project["id"])
        calls = len(llm.role_calls_received)
        response = await send(client, project["id"], "A")
        assert response.status_code == 200, response.text
        after = await _view(client, project["id"])
        assert after["run_id"] == before["run_id"] and after["status"] == "completed"
        assert len(visual.submit_calls) == 1 and len(llm.role_calls_received) == calls


async def test_mixed_answer_supplement_survives_a_model_delta(full_settings):
    def delta(request):
        message_id = request.payload["input_data"]["user_messages"][-1]["message_id"]
        return answer(
            request,
            updates=[
                {
                    "field": "style_keywords",
                    "operation": "set",
                    "value": ["深色背景"],
                    "evidence_ids": [message_id],
                }
            ],
        )

    async with _environment(
        settings_for(full_settings), ConsolidatedScript(answers=[first, delta])
    ) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做钢笔宣传图，送礼用")
        response = await send(client, project["id"], "A，但别太暗")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        brief = next(a["payload"] for a in view["artifacts"] if a["kind"] == "brief_draft")
        assert "深色背景" in brief["style_keywords"]
        assert "别太暗" in brief["creative_direction"]
        assert len(visual.submit_calls) == 1


def test_recovery_cannot_reinterpret_an_old_request_after_pause():
    original = intent({}, "帮我做钢笔宣传图", "request-message")
    paused = intent({"generation_intent": original}, "先别做", "pause-message")
    state = {"generation_intent": paused}
    assert intent(state, "帮我做钢笔宣传图", "request-message")["status"] == "paused"
    assert intent(state, "现在重新生成", "new-message")["status"] == "requested"


async def test_pause_during_failed_preparation_persists_and_requires_new_request(full_settings):
    async with _environment(settings_for(full_settings), ConsolidatedScript(fail_visual=True)) as (app, client, llm, visual):
        project = await _create_project(client)
        response = await client.post(f"/api/projects/{project['id']}/workflow/actions/submit_request", json={"expected_revision": 0, "text": "给钢笔做一张宣传图，冷白背景，不加文字"})
        assert response.status_code in (200, 503, 504)
        assert (await _view(client, project["id"]))["status"] == "failed"
        response = await send(client, project["id"], "先别做，先讨论一下")
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert visual.submit_calls == []
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)
        response = await send(client, project["id"], "现在直接制作")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assert len(visual.submit_calls) == 1
