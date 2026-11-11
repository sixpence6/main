from tests.support.prompt_confirmation import finish_with_prompt_confirmation

"""Regression coverage for collaborative revisions, attachments, copy and recovery."""

from uuid import uuid4

import pytest

from app.adapters.base import ProviderOutcome
from tests.test_role_chain_consolidation import ConsolidatedScript, _new_settings
from tests.test_v2_t05_requirements import (
    _create_project,
    _environment,
    _messages,
    _ready_data,
    _start,
    _view,
)
from tests.test_v2_t40_prompt_review import png_bytes


def settings_for(base):
    return type(base)(
        **{**_new_settings(base).model_dump(), "workflow_role_policy": "collaborative"}
    )


def answer(request, *, status="ready", kind="clarification", questions=None, updates=None, **brief):
    return {
        "role": "requirements_assistant",
        "status": status,
        "data": {
            "scope": "image_request",
            "kind": kind,
            "reference_usage": "none",
            "brief": _ready_data(request, facts=[], **brief),
            "updates": updates or [],
            "clarifications": questions or [],
        },
        "issues": (
            [
                {
                    "code": "MISSING_INPUT",
                    "path": (questions or [{"field": "product_description"}])[0]["field"],
                    "detail": "缺少必要信息",
                    "evidence_ids": [],
                }
            ]
            if status == "needs_input"
            else []
        ),
        "questions": [],
    }


async def send(client, project_id, content):
    view = await _view(client, project_id)
    return await client.post(
        f"/api/projects/{project_id}/messages",
        json={
            "content": content,
            "expected_revision": view["project_revision"],
            "request_id": str(uuid4()),
        },
    )


async def test_completed_chat_can_generate_requested_revision(full_settings):
    script = ConsolidatedScript()
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "一只米色托特包，极简冷白背景，不需要文字")
        await finish_with_prompt_confirmation(app, client)
        assert (await _view(client, project["id"]))["status"] == "completed"
        response = await send(client, project["id"], "把背景改成蓝色，重新生成一张")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assert len(visual.submit_calls) == 2, {
            "submits": len(visual.submit_calls),
            "assistant": (await _messages(client, project["id"]))[-1]["content"],
        }


async def test_missing_copy_stays_pending_when_subject_is_answered(full_settings):
    questions = [
        {
            "id": "subject",
            "field": "product_description",
            "text": "请问具体是什么商品？",
            "options": [],
        },
        {"id": "copy", "field": "texts", "text": "请提供要放到图上的文案原文。", "options": []},
    ]
    script = ConsolidatedScript(
        answers=[
            lambda r: answer(
                r, status="needs_input", questions=questions, product_description=None, texts=[]
            ),
            lambda r: answer(
                r,
                status="needs_input",
                questions=questions[1:],
                product_description="米色托特包",
                texts=[],
            ),
        ]
    )
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "给我的商品做张主图，要加一句促销文案")
        first = await _view(client, project["id"])
        assert first["stage"] == "S03" and first["status"] == "needs_input"
        response = await send(client, project["id"], "米色托特包")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["stage"] == "S03" and view["status"] == "needs_input", {
            "stage": view["stage"],
            "status": view["status"],
            "submits": len(visual.submit_calls),
        }


async def test_cancel_copy_while_waiting_for_subject_clears_previous_copy(full_settings):
    question = [
        {"id": "subject", "field": "product_description", "text": "具体是什么商品？", "options": []}
    ]
    script = ConsolidatedScript(
        answers=[
            lambda r: answer(
                r,
                status="needs_input",
                questions=question,
                product_description=None,
                texts=["新品上市"],
            ),
            lambda r: answer(r, product_description="米色托特包", texts=[]),
        ]
    )
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "为我的商品做张主图，写上新品上市")
        response = await send(client, project["id"], "商品是米色托特包，不要任何文字了")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        planner = next(
            r for r in llm.role_calls_received if r.payload["task"]["role"] == "visual_planner"
        )
        assert planner.payload["input_data"]["brief_draft"]["texts"] == [], planner.payload[
            "input_data"
        ]["brief_draft"]["texts"]


async def test_copy_cancellation_is_not_treated_as_literal_copy(full_settings):
    question = [
        {"id": "copy", "field": "texts", "text": "请提供要放到图上的文案原文。", "options": []}
    ]
    script = ConsolidatedScript(
        answers=[
            lambda r: answer(r, status="needs_input", questions=question, texts=[]),
            lambda r: answer(
                r,
                texts=[],
                updates=[
                    {
                        "field": "texts",
                        "operation": "clear",
                        "value": None,
                        "evidence_ids": [r.payload["task"]["allowed_evidence_ids"][-1]],
                    }
                ],
            ),
        ]
    )
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "米色托特包主图，要加文案")
        response = await send(client, project["id"], "不要文案了，保持无字")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        planner = next(
            r for r in llm.role_calls_received if r.payload["task"]["role"] == "visual_planner"
        )
        assert planner.payload["input_data"]["brief_draft"]["texts"] == [], planner.payload[
            "input_data"
        ]["brief_draft"]["texts"]


@pytest.mark.parametrize(
    "failed_role", ["requirements_assistant", "visual_planner", "generation_engineer"]
)
async def test_one_model_timeout_can_be_resumed_to_delivery(full_settings, failed_role):
    from app.adapters.base import ProviderError

    class FailsOnce(ConsolidatedScript):
        failed = False

        def responder(self, request):
            if request.payload["task"]["role"] == failed_role and not self.failed:
                self.failed = True
                raise ProviderError(
                    "controlled timeout", error_code="LLM_TIMEOUT", evidence="uncertain"
                )
            return super().responder(request)

    async with _environment(settings_for(full_settings), FailsOnce()) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        initial = await client.post(
            f"/api/projects/{project['id']}/workflow/actions/submit_request",
            json={
                "expected_revision": 0,
                "text": "一只米色托特包，不需要文字",
            },
        )
        assert initial.status_code in (200, 503, 504), initial.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "failed", view["status"]
        response = await client.post(
            f"/api/projects/{project['id']}/workflow/actions/continue_workflow",
            json={
                "expected_revision": view["project_revision"],
            },
        )
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", {
            "stage": view["stage"],
            "status": view["status"],
            "issue": view.get("issue"),
        }
        assert len(visual.submit_calls) == 1


class ReferenceScript(ConsolidatedScript):
    def __init__(self, *, subject_only_in_image=False):
        super().__init__()
        self.subject_only_in_image = subject_only_in_image

    def responder(self, request):
        role = request.payload["task"]["role"]
        if role == "reference_analyst":
            return {
                "role": role,
                "status": "ready",
                "data": {
                    "reference_usage": "product",
                    "observations": [
                        {
                            "kind": "product",
                            "description": "一只米色托特包",
                            "confidence": "clear",
                            "evidence_ids": request.payload["task"]["allowed_evidence_ids"],
                        }
                    ],
                    "texts": [],
                    "uncertainties": [],
                },
                "issues": [],
                "questions": [],
            }
        if role == "requirements_assistant":
            report = request.payload["input_data"].get("reference_report")
            if self.subject_only_in_image and not report:
                result = answer(
                    request,
                    status="needs_input",
                    kind="requirements_change",
                    product_description=None,
                    must_include=[],
                    questions=[
                        {
                            "id": "subject",
                            "field": "product_description",
                            "text": "请补充具体商品主体。",
                            "options": [],
                        }
                    ],
                )
            else:
                result = answer(request, kind="requirements_change")
            if not request.payload["input_data"].get("reference_asset_id") and not request.payload[
                "input_data"
            ].get("has_reference_attachment"):
                return result
            result["data"]["reference_usage"] = "product"
            result["data"]["brief"]["reference_usage"] = "product"
            result["data"]["brief"]["reference_asset_id"] = request.payload["input_data"].get(
                "reference_asset_id"
            )
            return result
        result = super().responder(request)
        if role == "visual_planner":
            result["data"]["visual"]["reference_treatment"] = "参考商品外观，不复制背景"
        return result


async def send_attachment(client, project_id, content):
    uploaded = await client.post(
        f"/api/projects/{project_id}/message-attachments",
        files={
            "file": ("product.png", png_bytes(64, 64), "image/png"),
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    view = await _view(client, project_id)
    return await client.post(
        f"/api/projects/{project_id}/messages",
        json={
            "content": content,
            "expected_revision": view["project_revision"],
            "request_id": str(uuid4()),
            "attachment_asset_id": uploaded.json()["asset"]["id"],
        },
    )


async def test_first_image_request_passes_analysis_to_requirements_before_asking(full_settings):
    async with _environment(
        settings_for(full_settings), ReferenceScript(subject_only_in_image=True)
    ) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        response = await send_attachment(
            client, project["id"], "按这张商品图帮我做一张电商主图，参考商品外观，不需要文字"
        )
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assistant_calls = [
            r
            for r in llm.role_calls_received
            if r.payload["task"]["role"] == "requirements_assistant"
        ]
        assert any(r.payload["input_data"].get("reference_report") for r in assistant_calls), {
            "roles": [r.payload["task"]["role"] for r in llm.role_calls_received],
            "assistant": (await _messages(client, project["id"]))[-1]["content"],
        }
        assert (await _view(client, project["id"]))["status"] == "completed"


async def test_replacing_image_can_generate_exactly_one_new_version(full_settings):
    async with _environment(settings_for(full_settings), ReferenceScript()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _start(client, project["id"], "米色托特包，不需要文字")
        await finish_with_prompt_confirmation(app, client)
        assert (await _view(client, project["id"]))["status"] == "completed"
        response = await send_attachment(
            client, project["id"], "换成这张商品图，参考商品外观，重新生成"
        )
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert response.status_code == 200, {
            "response": response.text,
            "stage": view["stage"],
            "status": view["status"],
            "roles": [r.payload["task"]["role"] for r in llm.role_calls_received],
            "submits": len(visual.submit_calls),
        }
        assert view["status"] == "completed", {"stage": view["stage"], "status": view["status"]}
        assert len(visual.submit_calls) == 2


async def test_reference_available_before_requirements_completes_full_delivery(full_settings):
    from tests.test_v2_t06_reference_requirements import _seed_replacement_asset

    async with _environment(
        settings_for(full_settings), ReferenceScript(subject_only_in_image=True)
    ) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _seed_replacement_asset(app, project["id"])
        view = await _view(client, project["id"])
        response = await client.post(
            f"/api/projects/{project['id']}/workflow/actions/submit_request",
            json={
                "expected_revision": view["project_revision"],
                "text": "参考商品图片生成电商主图，不需要文字",
            },
        )
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assert (await _view(client, project["id"]))["status"] == "completed"
        assert len(visual.submit_calls) == 1
        assert visual.submit_calls[0].reference_bytes
        assert [r.payload["task"]["role"] for r in llm.role_calls_received] == [
            "reference_analyst",
            "requirements_assistant",
            "visual_planner",
            "generation_engineer",
        ]
        assert visual.submit_calls[0].reference_path


async def test_completed_revision_keeps_new_prompt_history_and_request_idempotency(full_settings):
    from sqlalchemy import select

    from app.workflow.tables import Delivery, WorkflowRun

    class BlueBackground(ConsolidatedScript):
        def responder(self, request):
            result = super().responder(request)
            role = request.payload["task"]["role"]
            if role == "requirements_assistant":
                latest = request.payload["input_data"].get("pending_user_message", "")
                if "蓝色" in latest:
                    result["data"]["brief"]["style_keywords"] = ["极简", "蓝色背景"]
            elif role == "visual_planner":
                if "蓝色背景" in request.payload["input_data"]["brief_draft"]["style_keywords"]:
                    result["data"]["creative"]["direction"] = "蓝色背景突出米色托特包"
                    result["data"]["visual"]["background"] = "蓝色背景"
                    result["data"]["visual"]["palette"] = ["蓝色", "米色"]
            return result

    async with _environment(settings_for(full_settings), BlueBackground()) as (
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
        first = await _view(client, project["id"])
        assert first["status"] == "completed"
        original_prompt = visual.submit_calls[0].prompt
        body = {
            "content": "保留米色托特包，把背景改成蓝色，重新生成一张",
            "expected_revision": first["project_revision"],
            "request_id": str(uuid4()),
        }
        response = await client.post(f"/api/projects/{project['id']}/messages", json=body)
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        second = await _view(client, project["id"])
        assert second["status"] == "completed"
        assert len(visual.submit_calls) == 2
        assert "蓝色背景" in visual.submit_calls[1].prompt
        assert visual.submit_calls[0].prompt == original_prompt
        model_calls = len(llm.role_calls_received)
        replay = await client.post(f"/api/projects/{project['id']}/messages", json=body)
        assert replay.status_code == 200, replay.text
        await finish_with_prompt_confirmation(app, client)
        assert len(visual.submit_calls) == 2
        assert len(llm.role_calls_received) == model_calls
        async with app.state.session_factory() as session:
            runs = (
                await session.scalars(
                    select(WorkflowRun).where(WorkflowRun.project_id == project["id"])
                )
            ).all()
            deliveries = (
                await session.scalars(select(Delivery).where(Delivery.project_id == project["id"]))
            ).all()
        assert len(runs) == len(deliveries) == 2
        assert all(run.status == "completed" for run in runs)
        assert all(delivery.status == "published" for delivery in deliveries)
        for delivery in deliveries:
            image = await client.get(f"/api/assets/{delivery.asset_id}")
            assert image.status_code == 200


async def test_attachment_during_clarification_uses_report_and_submits_once(full_settings):
    async with _environment(
        settings_for(full_settings), ReferenceScript(subject_only_in_image=True)
    ) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        started = await _start(client, project["id"], "给我的商品做张电商主图，不需要文字")
        assert started["stage"] == "S03" and started["status"] == "needs_input"
        response = await send_attachment(
            client, project["id"], "商品就是这张图里的包，参考商品外观"
        )
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["status"] == "completed", view
        assert len(visual.submit_calls) == 1
        assert visual.submit_calls[0].reference_bytes
        roles = [request.payload["task"]["role"] for request in llm.role_calls_received]
        assert roles.count("reference_analyst") == 1
        assert roles.count("visual_planner") == 1
        assert roles.count("generation_engineer") == 1
        assistant_calls = [
            request
            for request in llm.role_calls_received
            if request.payload["task"]["role"] == "requirements_assistant"
        ]
        assert assistant_calls[-1].payload["input_data"]["reference_report"]


async def test_size_negotiation_preserves_unanswered_copy_requirement(full_settings):
    questions = [
        {
            "id": "subject",
            "field": "product_description",
            "text": "具体是什么商品？",
            "options": [],
        },
        {"id": "copy", "field": "texts", "text": "请提供促销文案原文。", "options": []},
    ]
    script = ConsolidatedScript(
        answers=[
            lambda r: answer(
                r,
                status="needs_input",
                questions=questions,
                product_description=None,
                must_include=[],
            ),
            lambda r: answer(
                r, status="needs_input", questions=questions[1:], product_description="米色托特包"
            ),
            lambda r: answer(r, texts=["新品上市"]),
        ]
    )
    async with _environment(settings_for(full_settings), script) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        first = await _start(client, project["id"], "给我的商品做张16:9主图，要加一句促销文案")
        assert first["status"] == "needs_input"
        response = await send(client, project["id"], "接受1024x1024，商品是米色托特包")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["stage"] == "S03" and view["status"] == "needs_input", {
            "stage": view["stage"],
            "status": view["status"],
            "submits": len(visual.submit_calls),
        }
        assert visual.submit_calls == []
        response = await send(client, project["id"], "新品上市")
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        assert (await _view(client, project["id"]))["status"] == "completed"
        assert len(visual.submit_calls) == 1
        assert "新品上市" in visual.submit_calls[0].prompt
