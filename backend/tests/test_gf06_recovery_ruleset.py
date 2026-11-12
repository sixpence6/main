"""GF-06: recovery must use the server-owned current rule catalogue.

The persisted reviewer request is historical evidence only. It may explain what
an older review saw, but it can never widen applicability, blocking strength,
evidence permissions, or select an unsupported contract for a new decision.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.db import write_transaction
from app.workflow.adjudication import RULESET_VERSION, SUPPORTED_RULESET_VERSIONS
from app.workflow.tables import DiagnosticEvent
from tests.test_v2_t40_prompt_review import (
    base_brief,
    candidate_data,
    engineer_response,
    environment,
    setup_confirmed_project,
    workflow_action,
)


def _check(
    target_id: str,
    rule_id: str,
    *,
    outcome: str = "pass",
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "outcome": outcome,
        "evidence_ids": [] if outcome == "not_applicable" else (evidence or [target_id]),
        "note": "仅按已确认快照核对",
    }


def _review(
    target_id: str, checks: list[dict[str, Any]], *, decision: str = "pass"
) -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {"target_id": target_id, "decision": decision, "checks": checks}
            ]
        },
        "issues": [],
        "questions": [],
    }


async def _recovery_checkpoint_id(app: Any, project_id: str) -> int:
    async with app.state.session_factory() as session:
        rows = (
            await session.scalars(
                select(DiagnosticEvent)
                .where(
                    DiagnosticEvent.project_id == project_id,
                    DiagnosticEvent.event == "recovery_checkpoint",
                )
                .order_by(DiagnosticEvent.id.desc())
            )
        ).all()
    for row in rows:
        detail = json.loads(row.detail_json)
        if (
            detail.get("stage") == "S07"
            and detail.get("phase") == "review"
            and detail.get("status") in {"error", "timeout", "invalid"}
        ):
            return row.id
    raise AssertionError("missing S07 recovery checkpoint")


async def _tamper_checkpoint(app: Any, event_id: int, mutate) -> None:
    async with write_transaction(app.state.session_factory) as session:
        event = await session.get(DiagnosticEvent, event_id)
        assert event is not None
        detail = json.loads(event.detail_json)
        request = json.loads(detail["request_json"])
        payload = request.get("payload")
        assert isinstance(payload, dict)
        mutate(payload)
        detail["request_json"] = json.dumps(request, ensure_ascii=False)
        event.detail_json = json.dumps(detail, ensure_ascii=False)
        await session.flush()


def _full_review(target_id: str, *, evidence: list[str] | None = None) -> dict[str, Any]:
    return _review(
        target_id,
        [
            _check(target_id, "G03_CONFIRMATION", evidence=evidence),
            _check(target_id, "G05_EXECUTION_BOUNDARY", evidence=evidence),
            _check(target_id, "advisory:aesthetics"),
            _check(target_id, "advisory:composition"),
            _check(target_id, "advisory:lighting"),
            _check(target_id, "advisory:creative"),
        ],
    )


async def _failed_compile(app: Any, client: Any, setup: dict[str, Any]) -> dict[str, Any]:
    response = await workflow_action(client, setup, "compile_prompt")
    assert response.status_code == 503, response.text
    view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
    assert view["stage"] == "S07"
    assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
    return view


@pytest.mark.asyncio
async def test_recovery_rebuilds_the_server_catalogue_not_the_tampered_history(full_settings):
    brief = base_brief()
    review_requests: list[Any] = []

    def reviewer(request):
        review_requests.append(request)
        target_id = request.payload["review_targets"][0]["target_id"]
        if len(review_requests) == 1:
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        # The tampered historical catalogue omitted G05. A reviewer that only
        # fills that weakened catalogue must not be able to release the prompt.
        return _review(target_id, [_check(target_id, "G03_CONFIRMATION")])

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=reviewer,
    ) as (app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        view = await _failed_compile(app, client, setup)
        checkpoint_id = await _recovery_checkpoint_id(app, setup["project_id"])
        async with app.state.session_factory() as session:
            event = await session.get(DiagnosticEvent, checkpoint_id)
            assert event is not None
            checkpoint_detail = json.loads(event.detail_json)
        assert checkpoint_detail["ruleset_version"] == RULESET_VERSION
        assert checkpoint_detail["target_kind"] == "prompt_plan"
        assert checkpoint_detail["target_id"]

        def weaken(payload: dict[str, Any]) -> None:
            input_data = payload["input_data"]
            input_data["ruleset_version"] = RULESET_VERSION
            input_data["rules"] = [
                rule
                for rule in input_data["rules"]
                if rule["rule_id"] != "G05_EXECUTION_BOUNDARY"
            ]
            for rule in input_data["rules"]:
                if rule["rule_id"] == "G03_CONFIRMATION":
                    rule["kind"] = "advisory"

        await _tamper_checkpoint(app, checkpoint_id, weaken)
        continued = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )

        assert continued.status_code == 409, continued.text
        assert continued.json()["error"]["code"] == "PROMPT_REVIEW_INVALID"
        # The replay was shown the server's current blocking catalogue, not
        # the weakened historical graph.
        recovered_input = review_requests[1].payload["input_data"]
        rules = {rule["rule_id"]: rule for rule in recovered_input["rules"]}
        assert recovered_input["ruleset_version"] == RULESET_VERSION
        assert rules["G05_EXECUTION_BOUNDARY"]["kind"] == "blocking"
        assert rules["G05_EXECUTION_BOUNDARY"]["applicability"] == "applicable"
        after = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert after["stage"] == "S07"
        assert not any(
            item["approval_type"] == "generation_prompt" for item in after["approvals"]
        )
        prompt = next(
            item for item in after["artifacts"] if item["kind"] == "prompt_plan"
        )
        assert prompt["status"] == "candidate"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_recovery_rejects_historical_evidence_permission_widening(full_settings):
    brief = base_brief()
    review_requests: list[Any] = []

    def reviewer(request):
        review_requests.append(request)
        target_id = request.payload["review_targets"][0]["target_id"]
        if len(review_requests) == 1:
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        return _full_review(target_id, evidence=["image:external"])

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=reviewer,
    ) as (app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        view = await _failed_compile(app, client, setup)
        checkpoint_id = await _recovery_checkpoint_id(app, setup["project_id"])

        def widen_evidence(payload: dict[str, Any]) -> None:
            payload["task"]["allowed_evidence_ids"] = ["image:external"]
            for rule in payload["input_data"]["rules"]:
                rule["evidence_types"] = ["image"]

        await _tamper_checkpoint(app, checkpoint_id, widen_evidence)
        continued = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )

        assert continued.status_code == 409, continued.text
        assert continued.json()["error"]["code"] == "PROMPT_REVIEW_INVALID"
        assert "image:external" not in review_requests[1].payload["task"][
            "allowed_evidence_ids"
        ]
        after = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert not any(
            item["approval_type"] == "generation_prompt" for item in after["approvals"]
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_unsupported_historical_ruleset_fails_closed(full_settings):
    brief = base_brief()
    review_requests: list[Any] = []

    def reviewer(request):
        review_requests.append(request)
        target_id = request.payload["review_targets"][0]["target_id"]
        if len(review_requests) == 1:
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        return _full_review(target_id)

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=reviewer,
    ) as (app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        view = await _failed_compile(app, client, setup)
        checkpoint_id = await _recovery_checkpoint_id(app, setup["project_id"])

        def unsupported(payload: dict[str, Any]) -> None:
            payload["input_data"]["ruleset_version"] = "v99.unsupported"

        await _tamper_checkpoint(app, checkpoint_id, unsupported)
        continued = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )

        assert continued.status_code == 409, continued.text
        assert continued.json()["error"]["code"] == "RULESET_UNSUPPORTED"
        assert len(review_requests) == 1  # no new review is attempted
        after = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert after["stage"] == "S07"
        assert after["status"] == "blocked"
        assert after["issue"]["code"] == "RULESET_UNSUPPORTED"
        assert visual.submit_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version", sorted(SUPPORTED_RULESET_VERSIONS))
async def test_every_supported_version_has_equivalent_normal_and_recovery_catalogues(
    full_settings, version: str
):
    brief = base_brief()
    normal_inputs: list[dict[str, Any]] = []

    def normal_reviewer(request):
        target_id = request.payload["review_targets"][0]["target_id"]
        normal_inputs.append(request.payload["input_data"])
        return _full_review(target_id)

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=normal_reviewer,
    ) as (_app, client, _llm, _visual, _registry):
        setup = await setup_confirmed_project(_app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S08"

    recovery_inputs: list[dict[str, Any]] = []
    review_calls = 0

    def recovery_reviewer(request):
        nonlocal review_calls
        review_calls += 1
        target_id = request.payload["review_targets"][0]["target_id"]
        recovery_inputs.append(request.payload["input_data"])
        if review_calls == 1:
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        return _full_review(target_id)

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=recovery_reviewer,
    ) as (app, client, _llm, _visual, _registry):
        setup = await setup_confirmed_project(app, client)
        view = await _failed_compile(app, client, setup)
        continued = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )
        assert continued.status_code == 200, continued.text
        assert continued.json()["stage"] == "S08"

    assert normal_inputs[0]["ruleset_version"] == version
    assert recovery_inputs[1]["ruleset_version"] == version
    assert normal_inputs[0]["rules"] == recovery_inputs[1]["rules"]
