from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.auth.models import Account
from app.models import Project
from app.timeutil import format_timestamp, utc_now
from app.workflow.reasons import (
    CandidateDefect,
    CandidateDefectCode,
    ReasonCode,
    classify_api_error,
    classify_candidate_defects,
    classify_issue_code,
    classify_review_decision,
    is_auto_repairable,
    repair_codes_from_defects,
)
from app.workflow.tables import (
    Artifact,
    DiagnosticEvent,
    GenerationJob,
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
from tests.test_v2_t40_prompt_review import candidate_data, engineer_response, png_bytes


def _defect(
    code: CandidateDefectCode, message: str = "任意文案", **kwargs
) -> CandidateDefect:
    return CandidateDefect(code, message, **kwargs)


def test_typed_reasons_do_not_depend_on_error_copy() -> None:
    first = [_defect(CandidateDefectCode.COVERAGE_MISMATCH, "文案一", rule_id="hard:subject")]
    second = [
        _defect(
            CandidateDefectCode.COVERAGE_MISMATCH, "完全不同的提示文字", rule_id="hard:subject"
        )
    ]
    assert classify_candidate_defects(first) is ReasonCode.REPAIRABLE_COMPOSITION
    assert classify_candidate_defects(second) is ReasonCode.REPAIRABLE_COMPOSITION
    assert is_auto_repairable(first) and is_auto_repairable(second)
    assert repair_codes_from_defects(first) == ("hard:subject",)


def test_typed_reason_taxonomy_covers_every_transition_class() -> None:
    content_removed = CandidateDefect(
        CandidateDefectCode.MISSING_COVERAGE,
        "缺少覆盖",
        rule_id="hard:subject",
        content_intact=False,
    )
    size_changed = CandidateDefect(
        CandidateDefectCode.SIZE_MISMATCH,
        "尺寸变化",
        content_intact=False,
    )
    assert classify_candidate_defects([content_removed]) is ReasonCode.TRUE_DRIFT
    assert classify_candidate_defects([size_changed]) is ReasonCode.TRUE_DRIFT
    assert classify_candidate_defects(
        [CandidateDefect(CandidateDefectCode.CONTRACT_INVALID, "契约错误", content_intact=False)]
    ) is ReasonCode.PROTOCOL_ERROR
    assert classify_review_decision("revise") is ReasonCode.REPAIRABLE_COMPOSITION
    assert classify_review_decision("reject") is ReasonCode.CONTENT_REJECTION
    assert classify_review_decision("unknown") is ReasonCode.MISSING_EVIDENCE
    assert classify_issue_code("MISSING_INPUT") is ReasonCode.MISSING_INFORMATION
    assert classify_issue_code("MISSING_EVIDENCE") is ReasonCode.MISSING_EVIDENCE
    assert classify_issue_code("REQUIREMENT_DRIFT") is ReasonCode.TRUE_DRIFT
    assert classify_issue_code("UNSAFE_REQUEST") is ReasonCode.CONTENT_REJECTION
    assert classify_issue_code("CONTRACT_ERROR") is ReasonCode.PROTOCOL_ERROR
    assert classify_issue_code("GENERATION_UNKNOWN") is ReasonCode.REMOTE_UNKNOWN
    assert classify_api_error("SERVICE_UNAVAILABLE") is ReasonCode.TECHNICAL_FAILURE
    assert classify_api_error("ACCOUNT_DISABLED") is ReasonCode.AUTHORIZATION_INVALID
    assert classify_api_error("IMAGE_NOT_APPROVED") is ReasonCode.MISSING_EVIDENCE


class _EngineerScript:
    def __init__(self, fake_attempts: int) -> None:
        self.fake_attempts = fake_attempts
        self.calls = 0

    def __call__(self, request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer":
            self.calls += 1
            brief = request.payload["approved_inputs"]["final_brief"]
            return engineer_response(
                candidate_data(brief, fake=self.calls <= self.fake_attempts)
            )
        return _generation_engineer_responder()(request)


async def _wait_role_calls(llm: LLMStub, expected: int) -> None:
    for _ in range(500):
        if len(llm.role_calls_received) >= expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {expected} role calls, got {len(llm.role_calls_received)}")


async def _run_state(app, project_id: str):
    async with app.state.session_factory() as session:
        run = await session.scalar(
            select(WorkflowRun).where(WorkflowRun.project_id == project_id)
        )
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
            await session.get(RevisionBudget, (run.id, "prompt_plan"))
            if run is not None
            else None
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
    parsed = []
    for item in diags:
        detail = json.loads(item.detail_json)
        if item.event in {"prompt_candidate_reserved", "prompt_candidate_approved"}:
            detail = {"action": item.event, **detail}
        parsed.append(detail)
    return run, artifacts, budget, parsed


@pytest.mark.asyncio
async def test_budget_is_reserved_before_model_and_repair_keeps_original_intent(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    script = _EngineerScript(fake_attempts=1)
    async with _environment(full_settings, llm, visual, responder=script) as (app, client):
        setup = await _setup_s06(app, client)
        gate = asyncio.Event()
        llm.gate = gate
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _wait_role_calls(llm, 1)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1 and len(slots) == 3
        assert all(slot.released_at is None for slot in slots)
        run_before, artifacts_before, budget_before, diags_before = await _run_state(
            app, setup["project_id"]
        )
        assert run_before is not None
        assert budget_before is not None and budget_before.used == 1
        assert artifacts_before == []
        assert [item["action"] for item in diags_before] == ["prompt_candidate_reserved"]
        assert diags_before[0]["attempt"] == 1

        gate.set()
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
        assert "prompt_candidate_reserved" in actions
        assert "candidate_invalid" in actions
        assert "auto_repair" in actions
        assert "prompt_candidate_approved" in actions
        invalid = next(item for item in diags if item["action"] == "candidate_invalid")
        assert invalid["reason"] == ReasonCode.REPAIRABLE_COMPOSITION
        assert visual.submit_calls and len(visual.submit_calls) == 1
        engineers = [
            item
            for item in llm.role_calls_received
            if item.payload["task"]["role"] == "generation_engineer"
        ]
        assert len(engineers) == 2


@pytest.mark.asyncio
async def test_three_invalid_text_candidates_exhaust_budget_without_image_retry(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    script = _EngineerScript(fake_attempts=99)
    async with _environment(full_settings, llm, visual, responder=script) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "accepted"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)
        run, artifacts, budget, diags = await _run_state(app, setup["project_id"])
        assert artifacts == []
        assert budget is not None and budget.used == 3
        assert [item["reason"] for item in diags if item["action"] == "candidate_invalid"] == [
            ReasonCode.REPAIRABLE_COMPOSITION
        ] * 3
        assert [item["action"] for item in diags].count("auto_repair") == 2
        assert visual.submit_calls == []
        engineers = [
            item
            for item in llm.role_calls_received
            if item.payload["task"]["role"] == "generation_engineer"
        ]
        assert len(engineers) == 3

        blocked = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/compile_prompt",
            json={"expected_revision": setup["revision"], "payload": {}},
        )
        assert blocked.status_code == 409, blocked.text
        assert "REVISION_BUDGET_EXHAUSTED" in blocked.text
        assert len(llm.role_calls_received) == 3
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_true_drift_still_abandons_intent_without_prompt_candidate(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(
        full_settings,
        llm,
        visual,
        responder=_generation_engineer_responder(extra_positive="限时特价 9.9 元"),
    ) as (app, client):
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


@pytest.mark.asyncio
async def test_unknown_image_submission_blocks_repair_before_another_model_call(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    script = _EngineerScript(fake_attempts=3)
    async with _environment(full_settings, llm, visual, responder=script) as (app, client):
        setup = await _setup_s06(app, client)
        gate = asyncio.Event()
        llm.gate = gate
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _wait_role_calls(llm, 1)
        async with app.state.session_factory() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
            assert run is not None
            now = format_timestamp(utc_now())
            session.add(
                GenerationJob(
                    id=str(uuid.uuid4()),
                    project_id=setup["project_id"],
                    run_id=run.id,
                    request_id=str(uuid.uuid4()),
                    request_digest="a" * 64,
                    confirmed_snapshot_digest="b" * 64,
                    status="unknown",
                    submit_attempted=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        gate.set()
        await _drain(app)

        assert len(llm.role_calls_received) == 1
        intents, _usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "accepted"
        assert all(slot.released_at is None for slot in slots)
        run, artifacts, _budget, diags = await _run_state(app, setup["project_id"])
        assert run is not None and run.status == "blocked"
        assert run.issue_code == "GENERATION_UNKNOWN"
        assert artifacts == []
        assert any(
            item["action"] == "stop_auto_advance"
            and item["reason"] == ReasonCode.REMOTE_UNKNOWN
            for item in diags
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_dependency_change_between_attempts_stops_before_second_call(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    script = _EngineerScript(fake_attempts=3)
    async with _environment(full_settings, llm, visual, responder=script) as (app, client):
        setup = await _setup_s06(app, client)
        gate = asyncio.Event()
        llm.gate = gate
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _wait_role_calls(llm, 1)
        async with app.state.session_factory() as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None
            project.revision += 1
            await session.commit()

        gate.set()
        await _drain(app)

        assert len(llm.role_calls_received) == 1
        run, artifacts, budget, diags = await _run_state(app, setup["project_id"])
        assert run is not None and run.stage == "S07"
        assert artifacts == []
        assert budget is not None and budget.used == 1
        assert any(
            item["action"] == "precondition_failed"
            and item["reason"] == ReasonCode.TRUE_DRIFT
            for item in diags
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_disabled_account_between_attempts_blocks_before_second_model_call(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    script = _EngineerScript(fake_attempts=3)
    async with _environment(full_settings, llm, visual, responder=script) as (app, client):
        setup = await _setup_s06(app, client)
        gate = asyncio.Event()
        llm.gate = gate
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _wait_role_calls(llm, 1)
        async with app.state.session_factory() as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None and project.owner_account_id is not None
            account = await session.get(Account, project.owner_account_id)
            assert account is not None
            account.is_disabled = 1
            account.disabled_at = format_timestamp(utc_now())
            await session.commit()

        gate.set()
        await _drain(app)

        assert len(llm.role_calls_received) == 1
        _run, artifacts, _budget, diags = await _run_state(app, setup["project_id"])
        assert artifacts == []
        assert any(
            item["action"] == "producer_failed"
            and item["reason"] == ReasonCode.AUTHORIZATION_INVALID
            for item in diags
        )
        assert visual.submit_calls == []
