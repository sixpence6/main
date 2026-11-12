import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderError, ProviderOutcome
from app.config import Settings
from app.workflow import requirements
from app.workflow.tables import WorkflowRun
from tests.support.prompt_confirmation import finish_with_prompt_confirmation
from tests.support.t01_prompt import responder as prompt_responder
from tests.support.t02_prompt import final_click
from tests.test_t65_generation_cycle import _rows
from tests.test_v2_t05_requirements import (
    Script,
    _act,
    _create_project,
    _environment,
    _messages,
    _needs_input,
    _ready_data,
    _start,
    _view,
)
from tests.test_v2_t08_visual_final_brief import creative_data, visual_data
from tests.test_v2_t40_prompt_review import png_bytes


async def test_sparse_partial_preserves_known_requirements(full_settings):
    def partial(request, *, sparse=False):
        data = _ready_data(
            request,
            product_description=None,
            purpose=None if sparse else "电商台面主图",
            style_keywords=[] if sparse else ["极简", "冷白背景"],
            composition=None if sparse else "台面主图",
            facts=[],
            must_include=[],
            assumptions=[],
        )
        return _needs_input(
            request,
            question="请说明这组新品的具体商品。",
            path="product_description",
            detail="商品待补充",
            data=data,
        )

    script = Script([lambda r: partial(r), lambda r: partial(r, sparse=True)])
    async with _environment(full_settings, script) as (_, client, stub, visual):
        project = await _create_project(client)
        started = await _start(
            client,
            project["id"],
            "为这组新品生成一张极简台面主图，冷白背景",
        )
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        first = (await _messages(client, project["id"]))[-1]["content"]
        assert "使用场景与目标：电商台面主图" in first, first
        assert "风格、构图与光影：极简、冷白背景" in first, first
        view = await _view(client, project["id"])
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1A 2B 3C",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        calls = [
            r.payload["input_data"]
            for r in stub.role_calls_received
            if r.payload["task"]["role"] == "requirements_consultant"
        ]
        assert len(calls) == 2
        assert calls[-1]["current_requirement"]["purpose"] == "电商台面主图"
        assert calls[-1]["current_requirement"]["style_keywords"] == ["极简", "冷白背景"]
        assert visual.submit_calls == []
        second = (await _messages(client, project["id"]))[-1]["content"]
        assert (
            "使用场景与目标：电商台面主图" in second
            and "风格、构图与光影：极简、冷白背景" in second
        ), second


def _new_settings(full_settings):
    return Settings(
        **{
            **full_settings.model_dump(),
            "workflow_role_policy": "consolidated",
            "workflow_gate_mode": "off",
        }
    )


class ConsolidatedScript:
    def __init__(self, *, answers=None, fail_visual=False):
        self.answers = list(answers or [])
        self.fail_visual = fail_visual
        self.assistant_calls = 0

    def responder(self, request):
        role = request.payload["task"]["role"]

        def ready(data):
            return {"role": role, "status": "ready", "data": data, "issues": [], "questions": []}

        if role == "requirements_assistant":
            index = self.assistant_calls
            self.assistant_calls += 1
            if self.answers:
                return self.answers[min(index, len(self.answers) - 1)](request)
            brief = _ready_data(request, style_keywords=["极简", "冷白背景"], facts=[])
            return ready(
                {
                    "scope": "image_request",
                    "kind": "requirements_change",
                    "reference_usage": "none",
                    "brief": brief,
                    "updates": [],
                    "clarifications": [],
                }
            )
        if role == "visual_planner":
            if self.fail_visual:
                self.fail_visual = False
                raise ProviderError(
                    "controlled timeout", error_code="LLM_TIMEOUT", evidence="uncertain"
                )
            brief = request.payload["input_data"]["brief_draft"]
            return ready(
                {
                    "creative": creative_data(direction="冷白台面突出米色托特包"),
                    "visual": visual_data(
                        style_keywords=["极简", "冷白背景"],
                        composition="一只米色托特包居中",
                        background="冷白台面与冷白背景",
                        palette=["冷白", "米色"],
                        lighting="柔和冷白光",
                        preservation_rules=brief["must_include"] + brief["must_preserve"],
                    ),
                }
            )
        if role == "generation_engineer":
            return prompt_responder(request)
        raise AssertionError(f"unexpected model role: {role}")


async def _action(client, project_id, view, action, payload=None):
    body = {"expected_revision": view["project_revision"]}
    if payload is not None:
        body["payload"] = payload
    response = await client.post(f"/api/projects/{project_id}/workflow/actions/{action}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def test_default_chain_has_three_model_calls_and_explicit_confirmations(full_settings):
    script = ConsolidatedScript()
    async with _environment(_new_settings(full_settings), script) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        view = await _start(
            client, project["id"], "一只米色托特包，极简冷白背景，电商主图，不需要文字"
        )
        assert view["stage"] == "S04", view
        assert [r.payload["task"]["role"] for r in llm.role_calls_received] == [
            "requirements_assistant"
        ]
        view = await _action(client, project["id"], view, "plan_creative")
        assert view["stage"] == "S06", view
        assert visual.submit_calls == []
        card = next(
            a for a in view["artifacts"] if a["kind"] == "final_brief" and a["status"] == "approved"
        )
        async with app.state.session_factory() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == project["id"])
            )
            assert run.role_policy == "consolidated"
            assert run.requirements_state_json
        view = await _action(
            client,
            project["id"],
            view,
            "confirm_requirements",
            {
                "artifact_id": card["id"],
                "artifact_digest": card["digest"],
                "requirement_revision": view["project_revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["prompt_preview"] is not None, view
        assert visual.submit_calls == []
        response = await final_click(client, {"project_id": project["id"], "workflow": view})
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["stage"] == "S10" and view["status"] == "needs_input", view
        assert len(visual.submit_calls) == 1
        assert [r.payload["task"]["role"] for r in llm.role_calls_received] == [
            "requirements_assistant",
            "visual_planner",
            "generation_engineer",
        ]
        view = await _action(client, project["id"], view, "approve_delivery")
        view = await _action(client, project["id"], view, "publish_delivery")
        assert view["status"] == "completed"
        intents, usages, _ = await _rows(app, project["id"])
        assert len(intents) == len(usages) == 1
        before = len(llm.role_calls_received)
        await _view(client, project["id"])
        await _view(client, project["id"])
        assert len(llm.role_calls_received) == before
        assert len(visual.submit_calls) == 1


async def test_combined_visual_recovery_replays_identical_request(full_settings):
    script = ConsolidatedScript(fail_visual=True)
    async with _environment(_new_settings(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "一只米色托特包，极简冷白背景，用于电商主图")
        response = await client.post(
            f"/api/projects/{project['id']}/workflow/actions/plan_creative",
            json={"expected_revision": view["project_revision"]},
        )
        assert response.status_code == 503, response.text
        view = await _view(client, project["id"])
        assert view["status"] == "failed"
        view = await _action(client, project["id"], view, "continue_workflow")
        assert view["stage"] == "S06", view
        calls = [
            r for r in llm.role_calls_received if r.payload["task"]["role"] == "visual_planner"
        ]
        assert len(calls) == 2
        assert calls[0].payload == calls[1].payload
        assert calls[0].system_prompt == calls[1].system_prompt
        assert visual.submit_calls == []


def test_sparse_update_and_clear_have_distinct_semantics():
    from types import SimpleNamespace

    previous = _ready_data(
        SimpleNamespace(
            payload={
                "task": {"allowed_evidence_ids": ["u1"]},
                "capabilities": {"size": "1024x1024"},
            }
        ),
        style_keywords=["冷白"],
        texts=["新品上市"],
    )
    sparse = {**previous, "purpose": None, "style_keywords": [], "texts": []}
    merged, _ = requirements.merge(
        previous, sparse, updates=[], evidence={"u2": "换成暖光"}, strict=True
    )
    assert merged.purpose == previous["purpose"]
    assert merged.style_keywords == ["冷白"] and merged.texts == ["新品上市"]
    merged, sources = requirements.merge(
        previous,
        sparse,
        updates=[
            {"field": "texts", "operation": "clear", "value": None, "evidence_ids": ["u2"]},
        ],
        evidence={"u2": "不需要文字"},
        strict=True,
    )
    assert merged.texts == [] and merged.style_keywords == ["冷白"]
    assert sources["texts"]["status"] == "resolved"
    with pytest.raises(Exception, match="清空需求需要用户明确说明"):
        requirements.merge(
            previous,
            sparse,
            updates=[
                {"field": "texts", "operation": "clear", "value": None, "evidence_ids": ["u2"]},
            ],
            evidence={"u2": "继续"},
            strict=True,
        )


def test_structured_options_bind_by_question_and_preserve_required_detail():
    questions = [
        {
            "id": "product-v1",
            "field": "product_description",
            "text": "请提供商品外观。",
            "options": [],
        },
        {
            "id": "text-v1",
            "field": "texts",
            "text": "画面文字如何处理？",
            "options": [
                {"id": "A", "label": "不需要文字", "value": "不需要文字", "requires_detail": False},
                {
                    "id": "C",
                    "label": "其他，请给出原文",
                    "value": "请提供文字原文",
                    "requires_detail": True,
                },
            ],
        },
    ]
    answers = requirements.parse_answers(questions, "1A 2C")
    assert len(answers) == 1
    assert answers[0]["question_id"] == "text-v1" and answers[0]["requires_detail"] is True
    assert requirements.parse_answers(questions, "2A")[0]["answer"] == "不需要文字"


async def test_structured_reply_survives_refresh_and_sparse_model_data(full_settings):
    questions = [
        {
            "id": "text-choice",
            "field": "texts",
            "text": "画面文字如何处理？",
            "options": [
                {"id": "A", "label": "不需要文字", "value": "不需要文字", "requires_detail": False},
                {
                    "id": "C",
                    "label": "其他，请给原文",
                    "value": "请提供文字原文",
                    "requires_detail": True,
                },
            ],
        },
        {
            "id": "product-description",
            "field": "product_description",
            "text": "请说明商品外观与数量。",
            "options": [],
        },
    ]

    def response(request, *, sparse=False):
        source = request.payload["task"]["allowed_evidence_ids"][-1]
        if sparse:
            answers = request.payload["input_data"]["clarification_answers"]
            assert any(
                a.get("question_id") == "text-choice" and a["answer"] == "不需要文字"
                for a in answers
            )
        return {
            "role": "requirements_assistant",
            "status": "needs_input",
            "data": {
                "scope": "image_request",
                "kind": "clarification",
                "reference_usage": "none",
                "brief": _ready_data(
                    request,
                    product_description=None,
                    facts=[],
                    must_include=[],
                    purpose=None if sparse else "电商台面主图",
                    style_keywords=[] if sparse else ["极简", "冷白背景"],
                ),
                "updates": [
                    {
                        "field": "texts",
                        "operation": "clear",
                        "value": None,
                        "evidence_ids": [source],
                    }
                ]
                if sparse
                else [],
                "clarifications": questions,
            },
            "issues": [
                {
                    "code": "MISSING_INPUT",
                    "path": "product_description",
                    "detail": "请补充商品外观",
                    "evidence_ids": [],
                }
            ],
            "questions": [],
        }

    script = ConsolidatedScript(answers=[lambda r: response(r), lambda r: response(r, sparse=True)])
    async with _environment(_new_settings(full_settings), script) as (_, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "新品极简台面主图，冷白背景")
        assert view["stage"] == "S03" and view["status"] == "needs_input", view
        # Reload from the database before answering; no in-memory question state is needed.
        view = await _view(client, project["id"])
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1A",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        report = (await _messages(client, project["id"]))[-1]["content"]
        assert "使用场景与目标：电商台面主图" in report
        assert "风格、构图与光影：极简、冷白背景" in report
        assert "画面文字：无画面文字（已确认）" in report
        assert "画面文字如何处理" not in report
        assert "1. 请说明商品外观与数量。" in report
        assert len(llm.role_calls_received) == 2
        assert visual.submit_calls == []


async def test_requirement_assistant_recovery_replays_identical_request(full_settings):
    def fail(request):
        raise ProviderError("controlled failure", error_code="LLM_TIMEOUT", evidence="uncertain")

    good = ConsolidatedScript()
    script = ConsolidatedScript(answers=[fail, good.responder])
    async with _environment(_new_settings(full_settings), script) as (_, client, llm, visual):
        project = await _create_project(client)
        response = await _act(
            client, project["id"], "submit_request", text="米色托特包电商主图", expected_revision=0
        )
        assert response.status_code == 503, response.text
        view = await _view(client, project["id"])
        view = await _action(client, project["id"], view, "continue_workflow")
        assert view["stage"] == "S04", view
        assert len(llm.role_calls_received) == 2
        assert llm.role_calls_received[0].payload == llm.role_calls_received[1].payload
        assert llm.role_calls_received[0].system_prompt == llm.role_calls_received[1].system_prompt
        assert visual.submit_calls == []


async def test_requirement_edit_uses_one_assistant_call_without_legacy_routers(full_settings):
    normal = ConsolidatedScript()

    def edit(request):
        data = request.payload["input_data"]["current_requirement"]
        return {
            "role": "requirements_assistant",
            "status": "ready",
            "data": {
                "scope": "image_request",
                "kind": "requirements_change",
                "reference_usage": "none",
                "brief": {**data, "style_keywords": ["暖色"]},
                "clarifications": [],
                "updates": [
                    {
                        "field": "style_keywords",
                        "operation": "set",
                        "value": ["暖色"],
                        "evidence_ids": ["current_user_message"],
                    }
                ],
            },
            "issues": [],
            "questions": [],
        }

    script = ConsolidatedScript(answers=[normal.responder, edit])
    async with _environment(_new_settings(full_settings), script) as (_, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "米色托特包，极简冷白背景，电商主图")
        view = await _action(client, project["id"], view, "plan_creative")
        assert view["stage"] == "S06"
        response = await client.post(
            f"/api/projects/{project['id']}/messages",
            json={
                "content": "只把风格改为暖色，其他保持不变",
                "expected_revision": view["project_revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200, response.text
        view = response.json()["workflow"]
        assert view["stage"] == "S04", view
        assert [r.payload["task"]["role"] for r in llm.role_calls_received] == [
            "requirements_assistant",
            "visual_planner",
            "requirements_assistant",
        ]
        brief = [
            a for a in view["artifacts"] if a["kind"] == "brief_draft" and a["status"] == "approved"
        ][-1]
        assert brief["payload"]["style_keywords"] == ["暖色"]
        assert brief["payload"]["product_description"] == "米色托特包"
        assert visual.submit_calls == []


def test_reference_change_retains_user_fields_and_invalidates_observations():
    import json
    from types import SimpleNamespace

    data = {
        "product_description": "米色托特包",
        "purpose": "电商主图",
        "style_keywords": ["冷白"],
        "composition": "原图左侧构图",
        "texts": [],
        "must_preserve": ["原图Logo"],
        "facts": [
            {"text": "米色托特包", "basis": "user_declared", "evidence_ids": ["u1"]},
            {"text": "原图Logo", "basis": "image_observed", "evidence_ids": ["reference:old"]},
        ],
        "reference_asset_id": "old",
        "reference_usage": "product",
        "unresolved": [],
    }
    stored = {
        "data": data,
        "field_sources": {
            key: {"status": "resolved", "evidence_ids": ["u1"]}
            for key in ("product_description", "style_keywords")
        },
    }
    ctx = SimpleNamespace(
        run=SimpleNamespace(requirements_state_json=json.dumps(stored)),
        project=SimpleNamespace(reference_asset_id="new"),
    )
    current = requirements.current_data(ctx, "product")
    assert current["product_description"] == "米色托特包" and current["style_keywords"] == ["冷白"]
    assert current["purpose"] == "电商主图"
    assert current["composition"] is None and current["must_preserve"] == []
    assert len(current["facts"]) == 1 and current["facts"][0]["basis"] == "user_declared"
    assert current["reference_asset_id"] == "new"


async def test_new_policy_setting_does_not_rewrite_existing_run_policy(full_settings):
    from app.db import write_transaction
    from app.workflow import state

    async with _environment(_new_settings(full_settings), ConsolidatedScript()) as (
        app,
        client,
        _,
        _,
    ):
        project = await _create_project(client)
        ctx = await state.load_context(app, project["id"])
        await state.create_run(ctx)
        async with write_transaction(app.state.session_factory) as session:
            run = await session.get(WorkflowRun, ctx.run_id)
            run.role_policy = "legacy"
        ctx = await state.reload_context(ctx)
        assert ctx.consolidated is False
        assert (await state.create_run(ctx)).role_policy == "legacy"
        new = await state.start_new_run(ctx)
        assert new.role_policy == "legacy"


def test_old_evidence_cannot_overwrite_newly_confirmed_field():
    from types import SimpleNamespace

    previous = _ready_data(
        SimpleNamespace(
            payload={
                "task": {"allowed_evidence_ids": ["u1"]},
                "capabilities": {"size": "1024x1024"},
            }
        )
    )
    with pytest.raises(Exception, match="本轮用户说明"):
        requirements.merge(
            previous,
            previous,
            updates=[
                {
                    "field": "style_keywords",
                    "operation": "set",
                    "value": ["暖色"],
                    "evidence_ids": ["u1"],
                },
            ],
            evidence={"u1": "以前选择暖色", "u2": "现在保留冷白"},
            strict=True,
        )


def test_conflict_requires_an_explicit_resolution():
    from types import SimpleNamespace

    previous = _ready_data(
        SimpleNamespace(
            payload={
                "task": {"allowed_evidence_ids": ["u1"]},
                "capabilities": {"size": "1024x1024"},
            }
        )
    )
    sources = {"product_description": {"status": "conflict", "evidence_ids": ["u1"]}}
    result, _ = requirements.merge(
        previous,
        previous,
        updates=[],
        evidence={"u1": "两种颜色冲突"},
        strict=True,
        prior_sources=sources,
    )
    assert "product_description" in result.unresolved


async def test_reference_branch_adds_only_one_optional_model_call(full_settings):
    from tests.test_v2_t06_reference_requirements import _seed_replacement_asset

    class WithReference(ConsolidatedScript):
        def responder(self, request):
            role = request.payload["task"]["role"]
            if role == "reference_analyst":
                return {
                    "role": role,
                    "status": "ready",
                    "data": {
                        "reference_usage": request.payload["input_data"]["reference_usage"],
                        "observations": [
                            {
                                "kind": "composition",
                                "description": "画面为纯色色块",
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
            result = super().responder(request)
            if role == "requirements_assistant":
                result["data"]["brief"]["reference_asset_id"] = request.payload["input_data"][
                    "reference_asset_id"
                ]
                result["data"]["brief"]["reference_usage"] = "product"
                result["data"]["reference_usage"] = "product"
            elif role == "visual_planner":
                result["data"]["visual"]["reference_treatment"] = "参考商品外观，不复制背景"
            return result

    async with _environment(_new_settings(full_settings), WithReference()) as (
        app,
        client,
        llm,
        visual,
    ):
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        project = await _create_project(client)
        await _seed_replacement_asset(app, project["id"])
        view = await _view(client, project["id"])
        response = await _act(
            client,
            project["id"],
            "submit_request",
            text="米色托特包用于电商主图，参考商品外观",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03", view
        view = await _action(client, project["id"], view, "compose_brief")
        view = await _action(client, project["id"], view, "plan_creative")
        assert view["stage"] == "S06", view
        assert [r.payload["task"]["role"] for r in llm.role_calls_received] == [
            "reference_analyst",
            "requirements_assistant",
            "visual_planner",
        ]
        assert visual.submit_calls == []
        card = next(
            a for a in view["artifacts"] if a["kind"] == "final_brief" and a["status"] == "approved"
        )
        view = await _action(
            client,
            project["id"],
            view,
            "confirm_requirements",
            {
                "artifact_id": card["id"],
                "artifact_digest": card["digest"],
                "requirement_revision": view["project_revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["prompt_preview"] is not None, view
        assert len(llm.role_calls_received) == 4 and visual.submit_calls == []
        response = await final_click(client, {"project_id": project["id"], "workflow": view})
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = await _view(client, project["id"])
        assert view["stage"] == "S10" and len(visual.submit_calls) == 1, view
        assert len(llm.role_calls_received) == 4


async def test_incomplete_option_cannot_become_ready_even_if_model_says_ready(full_settings):
    question = {
        "id": "copy-choice",
        "field": "texts",
        "text": "请选择画面文字。",
        "options": [
            {"id": "A", "label": "不需要文字", "value": "不需要文字", "requires_detail": False},
            {
                "id": "C",
                "label": "其他，请补充原文",
                "value": "请补充文字原文",
                "requires_detail": True,
            },
        ],
    }

    def first(request):
        return {
            "role": "requirements_assistant",
            "status": "needs_input",
            "data": {
                "scope": "image_request",
                "kind": "clarification",
                "reference_usage": "none",
                "brief": _ready_data(request, facts=[]),
                "updates": [],
                "clarifications": [question],
            },
            "issues": [
                {
                    "code": "MISSING_INPUT",
                    "path": "texts",
                    "detail": "尚缺文字原文",
                    "evidence_ids": [],
                }
            ],
            "questions": [],
        }

    def premature_ready(request):
        return {
            "role": "requirements_assistant",
            "status": "ready",
            "data": {
                "scope": "image_request",
                "kind": "clarification",
                "reference_usage": "none",
                "brief": _ready_data(request, facts=[], texts=["模型擅自补出的文案"]),
                "updates": [],
                "clarifications": [],
            },
            "issues": [],
            "questions": [],
        }

    script = ConsolidatedScript(answers=[first, premature_ready])
    async with _environment(_new_settings(full_settings), script) as (_, client, _, visual):
        project = await _create_project(client)
        view = await _start(
            client, project["id"], "米色托特包电商主图，希望画面有文字，还未确定原文"
        )
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1C",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert not any(a["kind"] == "brief_draft" for a in view["artifacts"])
        report = (await _messages(client, project["id"]))[-1]["content"]
        assert "请直接写出具体内容" in report
        assert "模型擅自补出的文案" not in report
        assert visual.submit_calls == []


def test_set_empty_cannot_bypass_explicit_clear_contract():
    from types import SimpleNamespace

    previous = _ready_data(
        SimpleNamespace(
            payload={
                "task": {"allowed_evidence_ids": ["u1"]},
                "capabilities": {"size": "1024x1024"},
            }
        ),
        texts=["已确认文字"],
    )
    with pytest.raises(Exception, match="clear 操作"):
        requirements.merge(
            previous,
            previous,
            updates=[
                {"field": "texts", "operation": "set", "value": [], "evidence_ids": ["u2"]},
            ],
            evidence={"u1": "已确认文字", "u2": "继续"},
            strict=True,
        )


def test_free_text_question_with_latin_letter_has_no_invented_option_example():
    from app.workflow.stages.s03 import _alignment_report

    question = {
        "id": "version",
        "field": "product_description",
        "text": "请说明产品 A 版本的外观。",
        "options": [],
    }
    report = _alignment_report(
        None,
        questions=[question["text"]],
        size="1024x1024",
        reference_asset_id=None,
        reference_usage="none",
        ready=False,
        clarifications=[question],
    )
    assert "例如「1A" not in report
    assert "请按编号用自然语言回答" in report


def test_pending_message_binding_never_rewrites_literal_product_copy():
    from app.workflow.consolidated import _bind_current_message

    alias = "current_user_message"
    value = {
        "brief": {"texts": [alias], "facts": [{"text": alias, "evidence_ids": [alias]}]},
        "updates": [{"field": "texts", "value": [alias], "evidence_ids": [alias]}],
    }
    bound = _bind_current_message(value, "real-user-message")
    assert bound["brief"]["texts"] == [alias]
    assert bound["brief"]["facts"][0]["text"] == alias
    assert bound["brief"]["facts"][0]["evidence_ids"] == ["real-user-message"]
    assert bound["updates"][0]["value"] == [alias]
    assert bound["updates"][0]["evidence_ids"] == ["real-user-message"]


def test_previous_final_card_is_projected_to_requirement_fields_before_merging():
    from types import SimpleNamespace

    proposed = _ready_data(
        SimpleNamespace(
            payload={
                "task": {"allowed_evidence_ids": ["u1"]},
                "capabilities": {"size": "1024x1024"},
            }
        )
    )
    final = {
        **proposed,
        "background": "冷白背景",
        "palette": ["白色"],
        "field_sources": {"purpose": "brief_draft"},
    }
    data, _ = requirements.merge(
        final, {**proposed, "purpose": None}, updates=[], evidence={"u1": "保持需求"}, strict=True
    )
    assert data.purpose == proposed["purpose"]
    assert data.product_description == proposed["product_description"]


async def test_legacy_explicit_no_text_clears_text_without_losing_other_fields(full_settings):
    def partial(request, *, cleared=False):
        return _needs_input(
            request,
            question="请补充商品外观。",
            path="product_description",
            detail="外观待补充",
            data=_ready_data(
                request,
                product_description=None,
                facts=[],
                must_include=[],
                purpose=None if cleared else "电商主图",
                style_keywords=[] if cleared else ["冷白"],
                texts=[] if cleared else ["旧文案"],
            ),
        )

    async with _environment(
        full_settings, Script([lambda r: partial(r), lambda r: partial(r, cleared=True)])
    ) as (app, client, _, _):
        project = await _create_project(client)
        view = await _start(client, project["id"], "冷白电商主图，画面文字写上旧文案")
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=view["project_revision"]
        )
        assert response.status_code == 200, response.text
        view = response.json()
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="不需要文字",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        from app.workflow import state

        ctx = await state.load_context(app, project["id"])
        data = requirements.current_data(ctx, "none")
        assert data["texts"] == [] and data["style_keywords"] == ["冷白"]
        assert data["purpose"] == "电商主图"


async def test_old_run_recovers_known_fields_from_earlier_validated_partials(full_settings):
    from app.workflow import state
    from app.workflow.stages.s03 import _previous_partial

    async with _environment(full_settings, Script([])) as (app, client, _, _):
        project = await _create_project(client)
        ctx = await state.load_context(app, project["id"])
        await state.create_run(ctx, "S03")
        from types import SimpleNamespace

        base = _ready_data(
            SimpleNamespace(
                payload={
                    "task": {"allowed_evidence_ids": ["u1"]},
                    "capabilities": {"size": "1024x1024"},
                }
            ),
            purpose="电商台面主图",
            style_keywords=["极简", "冷白"],
        )
        await state.record_diagnostic(
            ctx,
            "requirements_partial_validated",
            {"run_id": ctx.run_id, "reference_asset_id": None, "data": base},
        )
        await state.record_diagnostic(
            ctx,
            "requirements_partial_validated",
            {
                "run_id": ctx.run_id,
                "reference_asset_id": None,
                "data": {**base, "purpose": None, "style_keywords": []},
            },
        )
        recovered = await _previous_partial(ctx, "none")
        assert recovered["purpose"] == "电商台面主图" and recovered["style_keywords"] == [
            "极简",
            "冷白",
        ]


async def test_invalid_delta_preserves_state_and_recovery_replays_same_request(full_settings):
    def partial(request, *, invalid=False):
        source = request.payload["task"]["allowed_evidence_ids"][-1]
        return {
            "role": "requirements_assistant",
            "status": "needs_input",
            "data": {
                "scope": "image_request",
                "kind": "clarification",
                "reference_usage": "none",
                "brief": _ready_data(
                    request,
                    product_description=None,
                    facts=[],
                    must_include=[],
                    style_keywords=[] if invalid else ["冷白"],
                ),
                "updates": [
                    {
                        "field": "style_keywords",
                        "operation": "clear",
                        "value": None,
                        "evidence_ids": [source],
                    }
                ]
                if invalid
                else [],
                "clarifications": [
                    {
                        "id": "product",
                        "field": "product_description",
                        "text": "请补充商品外观。",
                        "options": [],
                    }
                ],
            },
            "issues": [
                {
                    "code": "MISSING_INPUT",
                    "path": "product_description",
                    "detail": "主体待补充",
                    "evidence_ids": [],
                }
            ],
            "questions": [],
        }

    script = ConsolidatedScript(
        answers=[lambda r: partial(r), lambda r: partial(r, invalid=True), lambda r: partial(r)]
    )
    async with _environment(_new_settings(full_settings), script) as (app, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "做一张冷白电商主图")
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="继续",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 409, response.text
        view = await _view(client, project["id"])
        assert view["status"] == "failed" and view["issue"]["code"] == "LLM_RESPONSE_INVALID"
        from app.workflow import state

        ctx = await state.load_context(app, project["id"])
        assert requirements.current_data(ctx, "none")["style_keywords"] == ["冷白"]
        view = await _action(client, project["id"], view, "continue_workflow")
        assert view["stage"] == "S03" and view["status"] == "needs_input"
        assert len(llm.role_calls_received) == 3
        assert llm.role_calls_received[1].payload == llm.role_calls_received[2].payload
        assert visual.submit_calls == []


async def test_revision_validation_failure_recovers_under_the_new_recorded_revision(full_settings):
    normal = ConsolidatedScript()

    def edit(request, *, invalid=False):
        brief = request.payload["input_data"]["current_requirement"]
        return {
            "role": "requirements_assistant",
            "status": "ready",
            "data": {
                "scope": "image_request",
                "kind": "requirements_change",
                "reference_usage": "none",
                "brief": {**brief, "style_keywords": ["暖色"]},
                "clarifications": [],
                "updates": [
                    {
                        "field": "style_keywords",
                        "operation": "clear" if invalid else "set",
                        "value": None if invalid else ["暖色"],
                        "evidence_ids": ["current_user_message"],
                    }
                ],
            },
            "issues": [],
            "questions": [],
        }

    script = ConsolidatedScript(answers=[normal.responder, lambda r: edit(r, invalid=True), edit])
    async with _environment(_new_settings(full_settings), script) as (_, client, llm, visual):
        project = await _create_project(client)
        view = await _start(client, project["id"], "米色托特包冷白电商主图")
        view = await _action(client, project["id"], view, "plan_creative")
        response = await client.post(
            f"/api/projects/{project['id']}/messages",
            json={
                "content": "只把风格改为暖色",
                "expected_revision": view["project_revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 409, response.text
        view = await _view(client, project["id"])
        assert view["stage"] == "S03" and view["status"] == "failed"
        view = await _action(client, project["id"], view, "continue_workflow")
        assert view["stage"] == "S04", view
        assert llm.role_calls_received[-1].payload == llm.role_calls_received[-2].payload
        brief = [
            a for a in view["artifacts"] if a["kind"] == "brief_draft" and a["status"] == "approved"
        ][-1]
        assert brief["payload"]["style_keywords"] == ["暖色"]
        assert visual.submit_calls == []
