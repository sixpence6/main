from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.workflow.reasons import ReasonCode
from app.workflow.tables import (
    Artifact,
    DiagnosticEvent,
    RevisionBudget,
    WorkflowRun,
)
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import (
    _drain,
    _environment,
    _rows,
    _setup_s06,
)
from tests.test_t66_replay_drift import (
    _confirmation_payload,
    _generation_engineer_responder,
)
from tests.test_v2_t40_prompt_review import (
    base_brief,
    candidate_data,
    engineer_response,
    environment,
    png_bytes,
    review_response,
    setup_confirmed_project,
    workflow_action,
)


def _brief_with(*, purpose: str | None = None, must_preserve: list[str] | None = None) -> dict:
    brief = base_brief()
    if purpose is not None:
        brief["purpose"] = purpose
    if must_preserve is not None:
        brief["must_preserve"] = must_preserve
    return brief


def _replace_prompt(data: dict, old: str, new: str) -> dict:
    data["positive_prompt"] = data["positive_prompt"].replace(old, new)
    return data


def _prompt_artifact(response) -> dict:
    view = response.json()
    return next(artifact for artifact in view["artifacts"] if artifact["kind"] == "prompt_plan")


def _reviewer_input(llm: LLMStub) -> dict:
    requests = [
        request
        for request in llm.role_calls_received
        if request.payload["task"]["role"] == "output_reviewer"
        and (request.payload.get("review_targets") or [{}])[0].get("target_kind") == "prompt_plan"
    ]
    assert requests, "prompt_plan independent review was not called"
    return requests[-1].payload["input_data"]


async def _run_state(app, project_id: str):
    async with app.state.session_factory() as session:
        run = await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id))
        artifacts = list(
            (
                await session.scalars(
                    select(Artifact).where(
                        Artifact.project_id == project_id,
                        Artifact.kind == "prompt_plan",
                    )
                )
            ).all()
        )
        budget = (
            await session.get(RevisionBudget, (run.id, "prompt_plan")) if run is not None else None
        )
        diags = list(
            (
                await session.scalars(
                    select(DiagnosticEvent).where(
                        DiagnosticEvent.project_id == project_id,
                        DiagnosticEvent.event.in_(
                            [
                                "prompt_transition",
                                "prompt_candidate_reserved",
                                "prompt_candidate_approved",
                            ]
                        ),
                    )
                )
            ).all()
        )
    import json

    parsed = []
    for item in diags:
        detail = json.loads(item.detail_json)
        if item.event in {"prompt_candidate_reserved", "prompt_candidate_approved"}:
            detail = {"action": item.event, **detail}
        parsed.append(detail)
    return run, artifacts, budget, parsed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "purpose",
    [
        "电商主图；用于大屏展示。突出质感\n背景简洁",
        "电商主图;用于大屏展示.突出质感\n背景简洁",
    ],
)
async def test_exact_multi_sentence_copy_with_punctuation_and_newline_is_covered(
    full_settings, purpose: str
) -> None:
    brief = _brief_with(purpose=purpose)
    data = candidate_data(brief)
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        approved = _prompt_artifact(response)
        assert approved["status"] == "approved"
        detail = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert detail["stage"] == "S08"
        assert _reviewer_input(llm)["coverage_explanations"] == []
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_missing_character_is_not_covered(full_settings) -> None:
    brief = base_brief()
    data = _replace_prompt(candidate_data(brief), "杯口金色描边", "杯口金色描")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert (
            len(
                [
                    item
                    for item in llm.role_calls_received
                    if item.payload["task"]["role"] == "generation_engineer"
                ]
            )
            == 1
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_numeric_quantity_change_is_not_false_passed(full_settings) -> None:
    brief = base_brief()
    data = _replace_prompt(candidate_data(brief), "350 毫升", "3500 毫升")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert (
            len(
                [
                    item
                    for item in llm.role_calls_received
                    if item.payload["task"]["role"] == "generation_engineer"
                ]
            )
            == 1
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_quantity_word_change_is_not_false_passed(full_settings) -> None:
    brief = _brief_with(must_preserve=["两件装"])
    data = _replace_prompt(candidate_data(brief), "两件装", "三件装")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("杯身无图案，保持白色", "杯身有图案，保持白色"),
        ("不要反光", "不要不反光"),
    ],
)
async def test_negation_change_is_not_covered(full_settings, old: str, new: str) -> None:
    brief = _brief_with(must_preserve=[old])
    data = _replace_prompt(candidate_data(brief), old, new)
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_cross_sentence_word_stitching_is_not_covered(full_settings) -> None:
    brief = _brief_with(purpose="电商主图；用于大屏展示")
    data = _replace_prompt(
        candidate_data(brief), "电商主图；用于大屏展示", "电商主图；用于品牌宣传大屏展示"
    )
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_merged_source_clauses_are_not_covered(full_settings) -> None:
    brief = _brief_with(purpose="电商主图；用于大屏展示")
    data = _replace_prompt(candidate_data(brief), "电商主图；用于大屏展示", "电商主图用于大屏展示")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert (
            len(
                [
                    item
                    for item in llm.role_calls_received
                    if item.payload["task"]["role"] == "generation_engineer"
                ]
            )
            == 1
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_requirement_split_across_clauses_is_not_covered(full_settings) -> None:
    brief = _brief_with(must_preserve=["杯口金色描边"])
    data = _replace_prompt(candidate_data(brief), "杯口金色描边", "杯口金色；描边")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_connective_wording_has_explainable_coverage_and_independent_review(
    full_settings,
) -> None:
    brief = base_brief()
    data = _replace_prompt(candidate_data(brief), "主体居中，留白充足", "主体居中，四周留白充足")
    _replace_prompt(data, "杯口金色描边", "杯口保留金色描边")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        assert _prompt_artifact(response)["status"] == "approved"
        notes = _reviewer_input(llm)["coverage_explanations"]
        assert any(note["explanation"] == "connective" for note in notes), notes
        assert {note["rule_id"] for note in notes} >= {
            "hard:composition",
            "hard:must_preserve:0",
        }
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_word_order_change_has_explainable_coverage_and_independent_review(
    full_settings,
) -> None:
    brief = _brief_with(purpose="电商主图；用于大屏展示")
    data = _replace_prompt(
        candidate_data(brief), "电商主图；用于大屏展示", "用于大屏展示；电商主图"
    )
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(request.payload["review_targets"][0]["target_id"]),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        assert _prompt_artifact(response)["status"] == "approved"
        notes = _reviewer_input(llm)["coverage_explanations"]
        assert any(note["explanation"] == "word_order" for note in notes), notes
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_word_order_change_is_rejected_by_independent_semantic_review(
    full_settings,
) -> None:
    brief = _brief_with(purpose="电商主图；用于大屏展示")
    data = _replace_prompt(
        candidate_data(brief), "电商主图；用于大屏展示", "用于大屏展示；电商主图"
    )
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"], decision="reject"
        ),
    ) as (_app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_REVIEW_REJECTED"
        notes = _reviewer_input(llm)["coverage_explanations"]
        assert any(note["explanation"] == "word_order" for note in notes), notes
        assert visual.submit_calls == []


class _OmissionThenExact:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer":
            self.calls += 1
            brief = request.payload["approved_inputs"]["final_brief"]
            data = candidate_data(brief)
            if self.calls == 1:
                data["positive_prompt"] = data["positive_prompt"].replace("；杯口金色描边", "")
                data["requirement_coverage"] = [
                    item
                    for item in data["requirement_coverage"]
                    if item["rule_id"] != "hard:must_preserve"
                ]
            return engineer_response(data)
        return _generation_engineer_responder()(request)


@pytest.mark.asyncio
async def test_true_omission_repairs_against_original_intent_and_reaches_image(
    full_settings,
) -> None:
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    script = _OmissionThenExact()
    async with _environment(full_settings, llm, visual, responder=script) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status != "abandoned"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)
        run, artifacts, budget, diags = await _run_state(app, setup["project_id"])
        assert run is not None and run.issue_code != "PROMPT_REQUIRES_RECONFIRMATION"
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        assert artifacts[0].revision_no == 2
        assert budget is not None and budget.used == 2
        actions = [item["action"] for item in diags]
        assert "candidate_invalid" in actions
        assert "auto_repair" in actions
        invalid = next(item for item in diags if item["action"] == "candidate_invalid")
        assert invalid["reason"] == ReasonCode.REPAIRABLE_COMPOSITION
        assert script.calls == 2
        assert len(visual.submit_calls) == 1

        workflow = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert workflow["stage"] == "S10"
        assert workflow["intent"]["id"] == intents[0].id


@pytest.mark.asyncio
async def test_dangerous_numeric_drift_returns_to_new_version(full_settings) -> None:
    llm = LLMStub()
    visual = VisualStub()
    brief = base_brief()
    data = _replace_prompt(candidate_data(brief), "350 毫升", "3500 毫升")

    def responder(request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer":
            return engineer_response(data)
        return _generation_engineer_responder()(request)

    async with _environment(full_settings, llm, visual, responder=responder) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "abandoned"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        run, artifacts, budget, diags = await _run_state(app, setup["project_id"])
        assert artifacts == []
        assert budget is not None and budget.used == 1
        assert run is not None and run.issue_code == "PROMPT_REQUIRES_RECONFIRMATION"
        reasons = [item["reason"] for item in diags if item["action"] == "reconfirm_requirements"]
        assert reasons == [ReasonCode.TRUE_DRIFT]
        assert visual.submit_calls == []
