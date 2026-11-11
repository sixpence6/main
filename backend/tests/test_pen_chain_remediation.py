from __future__ import annotations

import pytest

from app.workflow.stages.s03 import _alignment_report, _clarification_answers
from tests.support.prompt_confirmation import finish_with_prompt_confirmation
from tests.test_v2_t05_requirements import (
    Script,
    _act,
    _create_project,
    _environment,
    _messages,
    _ready,
    _start,
    _view,
)


def test_free_text_questions_do_not_advertise_unbound_codes():
    report = _alignment_report(
        None,
        questions=["请描述钢笔外观，例如：黑身银夹或全黑笔身。"],
        size="3840x2160",
        reference_asset_id=None,
        reference_usage="none",
        ready=False,
    )
    assert "例如「1A" not in report
    assert "服务默认" in report
    assert "商品主体：待补充" not in report


def test_codes_in_report_resolve_to_displayed_options():
    report = _alignment_report(
        None,
        questions=["请说明外观，例如：A. 黑身银夹；B. 全黑笔身。"],
        size="3840x2160",
        reference_asset_id=None,
        reference_usage="none",
        ready=False,
    )
    answers = _clarification_answers(report, "1A")
    assert answers[0]["answer"] == "黑身银夹"


@pytest.mark.parametrize("returned_size", ["3840x3840", "1024x1024"])
async def test_requested_square_stays_answerable_without_image_submission(
    full_settings, returned_size
):
    script = Script([lambda request: _ready(request, size=returned_size)])
    async with _environment(full_settings, script) as (app, client, stub, visual):
        project = await _create_project(client)
        started = await _start(
            client,
            project["id"],
            "做一张 1:1 促销主视觉，石墨黑底，留出价格位置。我要做一张钢笔图，用于电商主页",
        )
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        # With the test provider 1024x1024 is executable, so only its matching
        # ready output may proceed. A fabricated different size must clarify.
        if returned_size == "1024x1024":
            assert response.status_code == 200, response.text
            return
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "SIZE_UNSUPPORTED"
        assert "answer_requirements_clarification" in [a["id"] for a in view["available_actions"]]
        report = (await _messages(client, project["id"]))[-1]["content"]
        assert "1:1" in report
        assert "1024x1024" in report
        assert visual.submit_calls == []


@pytest.mark.parametrize("acceptance", ["1A", "我明确接受 1024x1024，保留其他要求。"])
async def test_model_cannot_silently_replace_requested_aspect_and_code_can_accept(
    full_settings, acceptance
):
    script = Script([_ready, _ready])
    async with _environment(full_settings, script) as (app, client, stub, visual):
        project = await _create_project(client)
        started = await _start(
            client, project["id"], "做一张 16:9 钢笔促销主视觉，石墨黑底，无文字，仅留价格空白。"
        )
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["issue"]["code"] == "SIZE_UNSUPPORTED"
        # Refresh preserves the exact displayed question-to-option binding.
        view = await _view(client, project["id"])
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text=acceptance,
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] != "failed"
        request = [
            r
            for r in stub.role_calls_received
            if r.payload["task"]["role"] == "requirements_consultant"
        ][-1]
        assert request.payload["input_data"]["size_requires_explicit_acceptance"] is False
        if acceptance == "1A":
            assert request.payload["input_data"]["clarification_answers"][-1]["answer"].startswith(
                "明确接受 1024x1024"
            )
        assert "石墨黑底，无文字，仅留价格空白" in request.payload["input_data"]["user_text"]
        assert visual.submit_calls == []


async def test_s07_projection_recovery_preserves_snapshot_requirements_and_single_usage(
    full_settings,
):
    import json
    import uuid

    from sqlalchemy import select

    from app.workflow.tables import DiagnosticEvent
    from tests.support.adapters import LLMStub, VisualStub
    from tests.test_t65_generation_cycle import (
        _environment as environment,
    )
    from tests.test_t65_generation_cycle import (
        _rows,
        _setup_s06,
    )
    from tests.test_t68_recovery import _confirm
    from tests.test_v3_p1_t08_recovery import _continue, _first_producer_failure_responder

    llm, visual = LLMStub(), VisualStub()
    async with environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        first = llm.role_calls_received[0].payload
        identity = first["approved_inputs"]["execution_snapshot"]
        assert identity["snapshot_digest"]
        assert (
            "constraints" not in identity and "fields" not in identity and "brief" not in identity
        )
        assert first["input_data"]["hard_requirements"]
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["issue"]["code"] == "LLM_TIMEOUT"
        assert view["task_state"]["reason"]["category"] == "technical_failure"
        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 200, response.text
        assert response.json()["prompt_preview"]["status"] == "awaiting_confirmation"
        assert llm.role_calls_received[1].payload == first
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == len(usages) == 1
        assert all(s.released_at is None for s in slots)
        assert visual.submit_calls == []
        async with app.state.session_factory() as session:
            events = list(
                (
                    await session.scalars(
                        select(DiagnosticEvent).where(
                            DiagnosticEvent.project_id == setup["project_id"]
                        )
                    )
                ).all()
            )
        metrics = [json.loads(e.detail_json) for e in events if e.event == "role_request_metrics"]
        assert len(metrics) >= 2
        assert metrics[0]["request_chars"] > 0 and metrics[0]["elapsed_seconds"] >= 0


async def test_synchronous_unknown_recovery_has_actionable_feedback_and_never_resubmits(
    full_settings,
):
    import uuid
    from dataclasses import replace

    from app.adapters.base import ProviderError
    from tests.support.adapters import LLMStub, VisualStub
    from tests.support.t02_prompt import final_click
    from tests.test_t65_generation_cycle import (
        _environment as environment,
    )
    from tests.test_t65_generation_cycle import (
        _rows,
        _setup_s06,
    )
    from tests.test_t68_recovery import _confirm, _normal_responder

    llm, visual = LLMStub(), VisualStub(submit_error=ProviderError("timeout", evidence="uncertain"))
    visual.capabilities = replace(visual.capabilities, asynchronous=False)
    async with environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        response = await final_click(client, {**setup, "workflow": view})
        assert response.status_code == 200, response.text
        await finish_with_prompt_confirmation(app, client)
        for action in ["verify_generation", "recover_generation", "recover_generation"]:
            response = await _act(
                client, setup["project_id"], action, expected_revision=setup["revision"]
            )
            assert response.status_code == 200, response.text
            view = response.json()
            assert view["issue"]["code"] == "GENERATION_UNKNOWN"
            assert view["issue"]["template_id"] == "RECOVERY_UNSUPPORTED"
            assert view["generation_job"]["id"] in view["issue"]["message"]
            assert view["generation_job"]["submit_attempted"] == 1
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == []
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == len(usages) == 1
        assert all(s.released_at is None for s in slots)


def test_natural_no_text_answer_is_preserved_in_report_and_authorization():
    from types import SimpleNamespace

    from app.workflow.stages.s03 import _text_authorized

    text = "完全不出现文字，仅留空白区域，价格由我后续添加。"
    report = _alignment_report(
        None,
        questions=["请描述钢笔外观。"],
        size="3840x2160",
        reference_asset_id=None,
        reference_usage="none",
        ready=False,
        user_text=text,
    )
    assert "画面文字：无画面文字（已确认）" in report
    assert "画面文字（如不需要文字" not in report
    assert _text_authorized([SimpleNamespace(content=text)]) is False
    assert (
        _text_authorized(
            [SimpleNamespace(content=text), SimpleNamespace(content="添加促销文字：新品上市。")]
        )
        is True
    )


async def test_validated_partial_context_survives_null_response_and_refresh(full_settings):
    from tests.test_v2_t05_requirements import _needs_input, _ready_data

    def first(request):
        data = _ready_data(request, product_description="钢笔", purpose="电商主页")
        return _needs_input(
            request, question="请说明钢笔外观。", path="composition", detail="外观待补充", data=data
        )

    script = Script(
        [
            first,
            lambda r: _needs_input(
                r, question="请说明钢笔外观。", path="composition", detail="外观待补充"
            ),
        ]
    )
    async with _environment(full_settings, script) as (app, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "钢笔，用于电商主页")
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        view = await _view(client, project["id"])
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="黑色磨砂笔身",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        consultant = [
            r
            for r in stub.role_calls_received
            if r.payload["task"]["role"] == "requirements_consultant"
        ][-1]
        assert (
            consultant.payload["input_data"]["current_requirement"]["product_description"] == "钢笔"
        )
        report = (await _messages(client, project["id"]))[-1]["content"]
        assert "上轮已核实的信息" in report
        assert "商品主体：钢笔" in report
        assert visual.submit_calls == []


async def test_size_choice_survives_later_appearance_answer(full_settings):
    from tests.test_v2_t05_requirements import _needs_input, _ready_data

    def ask_appearance(request):
        return _needs_input(
            request,
            question="请说明笔身外观。",
            path="composition",
            detail="外观待补充",
            data=_ready_data(request),
        )

    script = Script([_ready, ask_appearance, _ready])
    async with _environment(full_settings, script) as (app, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张 16:9 钢笔主视觉。")
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        view = response.json()
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1A",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        assert view["issue"]["code"] == "MISSING_INPUT"
        assert "用户期望 1024x1024" in (await _messages(client, project["id"]))[-1]["content"]
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="黑身银夹",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "active"
        assert visual.submit_calls == []


async def test_repeating_unsupported_ratio_keeps_details_without_model_retry(full_settings):
    from app.adapters.base import ProviderError

    script = Script(
        [
            _ready,
            ProviderError("invalid JSON", evidence="uncertain", error_code="LLM_RESPONSE_INVALID"),
        ]
    )
    async with _environment(full_settings, script) as (app, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张 16:9 钢笔图，用于电商主页。")
        response = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        view = response.json()
        response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="仍保留 16:9，黑色磨砂笔身、银色笔夹。完全不出现文字，价格由我后续添加。",
            expected_revision=view["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        assert view["status"] == "needs_input" and view["issue"]["code"] == "SIZE_UNSUPPORTED"
        assert script.brief_calls == 1
        report = (await _messages(client, project["id"]))[-1]["content"]
        assert "黑色磨砂笔身、银色笔夹" in report
        assert "画面文字：无画面文字（已确认）" in report
        assert "本轮补充已保存" in report
        assert visual.submit_calls == []
