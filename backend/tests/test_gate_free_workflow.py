import pytest

from app.adapters.base import ProviderOutcome
from tests.support.prompt_confirmation import finish_with_prompt_confirmation
from tests.test_role_chain_consolidation import ConsolidatedScript, _action, _new_settings
from tests.test_v2_t04_reference_analysis import responder as reference_responder
from tests.test_v2_t04_reference_analysis import upload_ok
from tests.test_v2_t05_requirements import (
    _act,
    _create_project,
    _environment,
    _messages,
    _ready_data,
    _start,
    _view,
)
from tests.test_v2_t40_prompt_review import png_bytes


async def test_four_roles_deliver_after_prompt_confirmation_without_business_reviews(full_settings):
    async with _environment(
        type(full_settings)(
            **{
                **_new_settings(full_settings).model_dump(),
                "workflow_role_policy": "collaborative",
                "workflow_gate_mode": "strict",
            }
        ),
        ConsolidatedScript(),
    ) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "一只米色托特包，极简冷白背景，电商主图，不需要文字")
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", view
        assert len(visual.submit_calls) == 1
        assert [r.payload["task"]["role"] for r in llm.role_calls_received] == [
            "requirements_assistant",
            "visual_planner",
            "generation_engineer",
        ]
        messages = await _messages(client, project["id"])
        assert all(
            "需求对齐情况" not in m["content"] and "回复方式" not in m["content"] for m in messages
        )
        await _view(client, project["id"])
        assert len(visual.submit_calls) == 1


def _direct_settings(settings):
    return type(settings)(
        **{**_new_settings(settings).model_dump(), "workflow_role_policy": "collaborative"}
    )


def _assistant(request, *, brief=None, questions=None, reply=""):
    questions = questions or []
    return {
        "role": "requirements_assistant",
        "status": "needs_input" if questions else "ready",
        "data": {
            "scope": "image_request",
            "kind": "clarification",
            "reference_usage": "none",
            "brief": brief or _ready_data(request, facts=[], must_preserve=[]),
            "updates": [],
            "clarifications": questions,
            "reply": reply,
        },
        "issues": [
            {
                "code": "MISSING_INPUT",
                "path": questions[0]["field"],
                "detail": "请补充",
                "evidence_ids": [],
            }
        ]
        if questions
        else [],
        "questions": [],
    }


async def test_bare_subject_gets_direction_guidance_without_optional_fact_forms(full_settings):
    def sparse(request):
        return _assistant(
            request,
            brief=_ready_data(
                request,
                product_description="钢笔",
                purpose=None,
                style_keywords=[],
                composition=None,
                lighting=None,
                facts=[],
                must_include=[],
                must_preserve=[],
                texts=[],
                unresolved=["must_preserve", "texts"],
            ),
            reply="请再告诉我必须保留哪些细节和画面文字。",
            questions=[
                {
                    "id": "preserve",
                    "field": "must_preserve",
                    "text": "有什么必须保留的细节？",
                    "options": [],
                },
                {"id": "texts", "field": "texts", "text": "需要什么文字？", "options": []},
            ],
        )

    async with _environment(
        _direct_settings(full_settings), ConsolidatedScript(answers=[sparse])
    ) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "我要生成一张钢笔图")
        pending = await _view(client, project["id"])
        assert pending["stage"] == "S03" and pending["status"] == "needs_input"
        assert pending["conversation_question"]["field"] == "purpose"
        assert len(visual.submit_calls) == 0
        response = await _act(client, project["id"], "answer_requirements_clarification",
                              text="你帮我选", expected_revision=pending["project_revision"])
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", view
        assert len(visual.submit_calls) == 1
        brief = next(a["payload"] for a in view["artifacts"] if a["kind"] == "brief_draft")
        assert brief["texts"] == brief["must_preserve"] == []
        assert brief["assumptions"] and brief["purpose"] == "商品展示"
        assert all("请再告诉我" not in m["content"] for m in await _messages(client, project["id"]))


@pytest.mark.parametrize(
    "copy, expected", [("商务钢笔 黑色金属笔夹", ["商务钢笔 黑色金属笔夹"]), ("不需要文字", [])]
)
async def test_saved_pen_choices_then_exact_copy_continue_without_reasking(
    full_settings, copy, expected
):
    def stale(request):
        return _assistant(
            request,
            brief=_ready_data(
                request,
                product_description="商务钢笔，黑色金属笔夹",
                facts=[],
                must_include=[],
                must_preserve=[],
                texts=[],
            ),
        )

    async with _environment(
        _direct_settings(full_settings), ConsolidatedScript(answers=[stale])
    ) as (app, client, llm, visual):
        project = await _create_project(client)
        # Seed through the existing strict interaction path to avoid manufacturing approvals.
        app.state.settings.workflow_role_policy = "consolidated"

        def old_questions(request):
            return _assistant(
                request,
                brief=_ready_data(
                    request,
                    product_description="商务钢笔，黑色金属笔夹",
                    facts=[],
                    must_include=[],
                    texts=[],
                    must_preserve=[],
                ),
                questions=[
                    {
                        "id": "preserve",
                        "field": "must_preserve",
                        "text": "是否有必须保留的外观？",
                        "options": [
                            {
                                "id": "A",
                                "label": "没有必须保留的细节",
                                "value": "没有必须保留的细节",
                                "requires_detail": False,
                            }
                        ],
                    },
                    {
                        "id": "copy",
                        "field": "texts",
                        "text": "画面需要文字吗？",
                        "options": [
                            {
                                "id": "A",
                                "label": "不需要文字",
                                "value": "不需要文字",
                                "requires_detail": False,
                            },
                            {
                                "id": "B",
                                "label": "需要商品名称，请提供原文",
                                "value": "需要商品名称，请提供原文",
                                "requires_detail": True,
                            },
                        ],
                    },
                ],
            )

        llm.role_responder = old_questions
        view = await _start(client, project["id"], "商务钢笔，暖色书桌，用于详情页")
        assert view["status"] == "needs_input", view
        app.state.settings.workflow_role_policy = "collaborative"
        llm.role_responder = ConsolidatedScript(answers=[stale]).responder
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1A 2B",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "needs_input", view
        content = (await _messages(client, project["id"]))[-1]["content"]
        assert "文字原文" in content and "保留" not in content
        assert "需求对齐情况" not in content and "回复方式" not in content
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text=copy,
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", view
        brief = next(a["payload"] for a in view["artifacts"] if a["kind"] == "brief_draft")
        assert brief["texts"] == expected
        assert brief["must_preserve"] == []
        assert len(visual.submit_calls) == 1


async def test_reference_role_joins_and_delivery_has_no_review_calls(full_settings):
    base = ConsolidatedScript()
    ref = reference_responder()

    def respond(request):
        role = request.payload["task"]["role"]
        if role == "reference_analyst":
            return ref(request)
        result = base.responder(request)
        if role == "requirements_assistant":
            result["data"]["reference_usage"] = "product"
            result["data"]["brief"]["reference_asset_id"] = request.payload["input_data"][
                "reference_asset_id"
            ]
            result["data"]["brief"]["reference_usage"] = "product"
        return result

    class Script:
        responder = staticmethod(respond)

    async with _environment(_direct_settings(full_settings), Script()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        uploaded = await upload_ok(client, project["id"], 0, png_bytes(40, 20))
        response = await _act(
            client,
            project["id"],
            "submit_request",
            text="参考这只米色托特包，制作电商主图，极简白色背景",
            expected_revision=uploaded["revision"],
        )
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", view
        assert {r.payload["task"]["role"] for r in llm.role_calls_received} == {
            "reference_analyst",
            "requirements_assistant",
            "visual_planner",
            "generation_engineer",
        }
        assert len(llm.role_calls_received) == 4
        assert len(visual.submit_calls) == 1


async def test_model_error_is_specific_and_recovery_does_not_duplicate_images(full_settings):
    base = ConsolidatedScript()
    count = 0

    def respond(request):
        nonlocal count
        if request.payload["task"]["role"] == "requirements_assistant":
            count += 1
            if count == 1:
                return {
                    "role": "requirements_assistant",
                    "status": "ready",
                    "data": {"broken": True},
                    "issues": [],
                    "questions": [],
                }
        return base.responder(request)

    class Script:
        responder = staticmethod(respond)

    async with _environment(_direct_settings(full_settings), Script()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        response = await _act(
            client, project["id"], "submit_request", text="米色托特包主图", expected_revision=0
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LLM_RESPONSE_INVALID"
        assert visual.submit_calls == []
        view = await _view(client, project["id"])
        await _action(client, project["id"], view, "continue_workflow")
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", view
        assert len(visual.submit_calls) == 1


async def test_refresh_during_prompt_compilation_does_not_offer_a_second_driver(full_settings):
    import asyncio

    base = ConsolidatedScript()
    started, release = asyncio.Event(), asyncio.Event()
    compiler_calls = 0

    async with _environment(_direct_settings(full_settings), base) as (app, client, llm, visual):
        original_complete = llm.complete

        async def complete(request):
            nonlocal compiler_calls
            if request.payload["task"]["role"] == "generation_engineer":
                compiler_calls += 1
                started.set()
                await release.wait()
            return await original_complete(request)

        llm.complete = complete
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "米色托特包主图")
        await asyncio.wait_for(started.wait(), timeout=5)
        try:
            for _ in range(2):
                view = await _view(client, project["id"])
                assert view["stage"] == "S07" and view["status"] == "active", view
                assert not any(a["id"] == "compile_prompt" for a in view["available_actions"])
            await _action(client, project["id"], view, "compile_prompt")
            assert compiler_calls == 1
        finally:
            release.set()
        await finish_with_prompt_confirmation(app, client)
        assert (await _view(client, project["id"]))["status"] == "completed"
        assert len(visual.submit_calls) == 1


async def test_old_reviewer_failure_resumes_with_only_the_current_four_roles(full_settings):
    from tests.test_v2_t05_requirements import Script, _ready

    legacy = Script([_ready], review_modes=["provider_error"])
    async with _environment(full_settings, legacy) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "米色托特包，电商主图，不需要文字")
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=view["project_revision"]
        )
        assert response.status_code == 503, response.text
        before = await _view(client, project["id"])
        assert before["status"] == "failed"
        calls = len(llm.role_calls_received)
        app.state.settings.workflow_role_policy = "collaborative"
        llm.role_responder = ConsolidatedScript().responder
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        await _action(client, project["id"], before, "continue_workflow")
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["run_id"] == before["run_id"]
        assert view["status"] == "completed", view
        assert [r.payload["task"]["role"] for r in llm.role_calls_received[calls:]] == [
            "requirements_assistant",
            "visual_planner",
            "generation_engineer",
        ]
        assert len(visual.submit_calls) == 1
