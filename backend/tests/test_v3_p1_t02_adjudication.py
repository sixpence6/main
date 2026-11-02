"""P1/T02 (#88): one server rule catalogue and one effective adjudication.

WG3-AC-01/02/03 are pinned twice: as pure adjudication semantics, and at the
S07 HTTP seam for both the normal and the recovery path.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.base import ProviderError
from app.workflow.adjudication import (
    CONTRADICTION,
    DUPLICATE_INSTANCE,
    ILLEGAL_EVIDENCE,
    MISSING_RESULT,
    RULESET_VERSION,
    UNKNOWN_RULE,
    WRONG_TARGET,
    Check,
    ProtocolIssue,
    Rule,
    RuleSet,
    adjudicate,
    s07_rules,
)
from tests.test_v2_t40_prompt_review import (
    base_brief,
    candidate_data,
    engineer_response,
    environment,
    setup_confirmed_project,
    workflow_action,
)

TARGET = "plan-1"


def _ruleset(*, advisory: bool = True) -> RuleSet:
    rules = [
        Rule("G03_CONFIRMATION", "提示词与确认需求一致"),
        Rule("G05_EXECUTION_BOUNDARY", "提示词未越出执行边界"),
    ]
    if advisory:
        rules.append(Rule("advisory:aesthetics", "综合观感", kind="advisory"))
    return RuleSet(target_kind="prompt_plan", rules=tuple(rules))


def _check(
    rule_id: str,
    *,
    outcome: str = "pass",
    evidence: tuple[str, ...] = (TARGET,),
    instance: str = "",
) -> Check:
    return Check(rule_id, outcome, evidence, "仅按已确认快照核对", instance)


def _adjudicate(
    checks: list[Check],
    *,
    decision: str = "pass",
    allowed: tuple[str, ...] = (TARGET,),
    ruleset: RuleSet | None = None,
    target: str = TARGET,
    result_target: str | None = None,
):
    return adjudicate(
        ruleset or _ruleset(),
        target_id=target,
        result_target_id=result_target or target,
        declared_decision=decision,
        checks=[check.as_payload() for check in checks],
        allowed_evidence=allowed,
    )


# --------------------------------------------------------------------------
# Rule catalogue semantics (WG3-AC-01 / 02 / 03)
# --------------------------------------------------------------------------


def test_catalogue_separates_blocking_and_advisory_rules() -> None:
    catalog = s07_rules()

    assert catalog.version == RULESET_VERSION
    assert [rule.rule_id for rule in catalog.blocking()] == [
        "G03_CONFIRMATION",
        "G05_EXECUTION_BOUNDARY",
    ]
    assert "advisory:aesthetics" in {rule.rule_id for rule in catalog.advisory()}
    for rule in catalog.blocking():
        assert rule.applicable is True


def test_all_not_applicable_blocking_rules_cannot_open_the_gate() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_CONFIRMATION", outcome="not_applicable", evidence=()),
                _check("G05_EXECUTION_BOUNDARY", outcome="not_applicable", evidence=()),
            ]
        )

    assert caught.value.code == CONTRADICTION
    assert "由服务端判定适用" in caught.value.detail


def test_advisory_results_never_change_a_satisfied_blocking_gate() -> None:
    for outcome in ("pass", "fail", "unknown", "not_applicable"):
        evidence = (TARGET,) if outcome == "pass" else ()
        result = _adjudicate(
            [
                _check("G03_CONFIRMATION"),
                _check("G05_EXECUTION_BOUNDARY"),
                _check("advisory:aesthetics", outcome=outcome, evidence=evidence),
            ]
        )

        assert result.decision == "pass"
        assert result.approved is True
        assert result.advisory == {"advisory:aesthetics": outcome}
        assert result.blocking_failures == ()


def test_advisory_pass_cannot_rescue_a_failing_blocking_rule() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_CONFIRMATION", outcome="fail"),
                _check("G05_EXECUTION_BOUNDARY"),
                _check("advisory:aesthetics"),
            ]
        )

    assert caught.value.code == CONTRADICTION


def test_unknown_rule_is_a_protocol_issue_not_a_gate_pass() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_CONFIRMATION"),
                _check("G05_EXECUTION_BOUNDARY"),
                _check("review:made_up"),
            ]
        )

    assert caught.value.code == UNKNOWN_RULE


def test_missing_blocking_result_is_rejected() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate([_check("G03_CONFIRMATION")])

    assert caught.value.code == MISSING_RESULT


def test_evidence_must_come_from_the_allowed_set_and_type() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_CONFIRMATION", evidence=("image:someone-else",)),
                _check("G05_EXECUTION_BOUNDARY"),
            ]
        )
    assert caught.value.code == ILLEGAL_EVIDENCE

    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [_check("G03_CONFIRMATION", evidence=("image:1",)), _check("G05_EXECUTION_BOUNDARY")],
            allowed=(TARGET, "image:1"),
        )
    assert caught.value.code == ILLEGAL_EVIDENCE


def test_failing_rule_without_evidence_is_unproven() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_CONFIRMATION", outcome="fail", evidence=()),
                _check("G05_EXECUTION_BOUNDARY"),
            ]
        )

    assert caught.value.code == ILLEGAL_EVIDENCE


def test_wrong_target_is_rejected() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [_check("G03_CONFIRMATION"), _check("G05_EXECUTION_BOUNDARY")],
            result_target="plan-2",
        )

    assert caught.value.code == WRONG_TARGET


def test_declared_pass_cannot_hide_a_blocking_failure() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate([_check("G03_CONFIRMATION", outcome="fail"), _check("G05_EXECUTION_BOUNDARY")])

    assert caught.value.code == CONTRADICTION


def test_declared_fail_with_all_blocking_pass_is_rejected() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [_check("G03_CONFIRMATION"), _check("G05_EXECUTION_BOUNDARY")], decision="revise"
        )

    assert caught.value.code == CONTRADICTION


def test_duplicate_rule_instance_is_rejected() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_CONFIRMATION"),
                _check("G03_CONFIRMATION"),
                _check("G05_EXECUTION_BOUNDARY"),
            ]
        )

    assert caught.value.code == DUPLICATE_INSTANCE


def test_each_rule_instance_is_checked_individually() -> None:
    catalog = RuleSet(
        target_kind="brief_draft",
        rules=(
            Rule("hard:text", "文案逐条覆盖", instance="0"),
            Rule("hard:text", "文案逐条覆盖", instance="1"),
        ),
    )

    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate([_check("hard:text", instance="0")], ruleset=catalog)

    assert caught.value.code == MISSING_RESULT

    result = _adjudicate(
        [_check("hard:text", instance="0"), _check("hard:text", instance="1")],
        ruleset=catalog,
    )
    assert result.decision == "pass"


def test_server_proven_non_applicability_skips_the_rule_but_cannot_be_filled() -> None:
    catalog = RuleSet(
        target_kind="quality_report",
        rules=(
            Rule("hard:product_consistency", "产品一致性", applicable=False, reason="画面无产品"),
            Rule("hard:size_format", "尺寸格式"),
        ),
    )

    result = _adjudicate([_check("hard:size_format")], ruleset=catalog)
    assert result.decision == "pass"

    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("hard:product_consistency"),
                _check("hard:size_format"),
            ],
            ruleset=catalog,
        )
    assert caught.value.code == CONTRADICTION


def test_ruleset_payload_round_trips_for_recovery() -> None:
    catalog = _ruleset()
    restored = RuleSet(
        target_kind=catalog.target_kind,
        rules=tuple(
            Rule(
                item["rule_id"],
                item["label"],
                kind=item["kind"],  # type: ignore[arg-type]
                evidence_types=tuple(item["evidence_types"]),
                applicable=item["applicability"] == "applicable",
                reason=item["reason"],
                instance=item["instance"],
            )
            for item in catalog.describe()
        ),
        version=catalog.version,
    )

    assert [(rule.rule_id, rule.kind) for rule in restored.rules] == [
        (rule.rule_id, rule.kind) for rule in catalog.rules
    ]


# --------------------------------------------------------------------------
# S07 HTTP seam
# --------------------------------------------------------------------------


def _review(
    target_id: str, checks: list[dict[str, Any]], *, decision: str = "pass"
) -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {"results": [{"target_id": target_id, "decision": decision, "checks": checks}]},
        "issues": [],
        "questions": [],
    }


def _reviewer(*items: tuple[str, str], decision: str = "pass"):
    """Reviewer double that fills the catalogue with the given rule outcomes."""

    def respond(request):
        target_id = request.payload["review_targets"][0]["target_id"]
        checks = [
            _payload(target_id, rule_id, outcome=outcome) for rule_id, outcome in items
        ]
        return _review(target_id, checks, decision=decision)

    return respond


def _payload(
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


@pytest.mark.asyncio
async def test_s07_all_not_applicable_is_rejected_without_approval_or_image(full_settings):
    brief = base_brief()

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=_reviewer(
            ("G03_CONFIRMATION", "not_applicable"),
            ("G05_EXECUTION_BOUNDARY", "not_applicable"),
        ),
    ) as (app, client, llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_REVIEW_INVALID"
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        prompt = next(item for item in view["artifacts"] if item["kind"] == "prompt_plan")
        assert prompt["status"] == "candidate"
        assert prompt["payload"] is None
        assert not any(item["approval_type"] == "generation_prompt" for item in view["approvals"])
        assert len(llm.role_calls_received) == 2


@pytest.mark.asyncio
async def test_s07_advisory_failure_does_not_block_an_approved_prompt(full_settings):
    brief = base_brief()

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=_reviewer(
            ("G03_CONFIRMATION", "pass"),
            ("G05_EXECUTION_BOUNDARY", "pass"),
            ("advisory:aesthetics", "fail"),
            ("advisory:lighting", "unknown"),
            ("advisory:creative", "pass"),
            ("advisory:composition", "not_applicable"),
        ),
    ) as (app, client, _llm, visual, registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")

        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S08"
        prompt = next(item for item in view["artifacts"] if item["kind"] == "prompt_plan")
        assert prompt["status"] == "approved"
        assert any(item["approval_type"] == "generation_prompt" for item in view["approvals"])
        assert visual.submit_calls == []
        assert registry.recorder.visual_builds == 0


@pytest.mark.asyncio
async def test_s07_unknown_rule_is_a_typed_protocol_rejection(full_settings):
    brief = base_brief()

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=_reviewer(
            ("G03_CONFIRMATION", "pass"),
            ("G05_EXECUTION_BOUNDARY", "pass"),
            ("review:invented_rule", "pass"),
        ),
    ) as (app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")

        assert response.status_code == 409, response.text
        body = response.json()["error"]
        assert body["code"] == "PROMPT_REVIEW_INVALID"
        assert "未登记规则" in body["message"]
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert not any(item["approval_type"] == "generation_prompt" for item in view["approvals"])


@pytest.mark.asyncio
async def test_s07_blocking_failure_keeps_the_gate_closed(full_settings):
    brief = base_brief()

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(candidate_data(brief)),
        reviewer=_reviewer(
            ("G03_CONFIRMATION", "fail"),
            ("G05_EXECUTION_BOUNDARY", "pass"),
            ("advisory:aesthetics", "pass"),
            decision="revise",
        ),
    ) as (app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(app, client)
        response = await workflow_action(client, setup, "compile_prompt")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_REVIEW_REJECTED"
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert not any(item["approval_type"] == "generation_prompt" for item in view["approvals"])


@pytest.mark.asyncio
async def test_s07_recovery_replays_same_candidate_with_same_catalogue(full_settings):
    brief = base_brief()
    engineer_calls: list[int] = []
    review_calls: list[int] = []

    def engineer(_request):
        engineer_calls.append(1)
        return engineer_response(candidate_data(brief))

    def reviewer(request):
        review_calls.append(1)
        target_id = request.payload["review_targets"][0]["target_id"]
        # The catalogue the reviewer was asked to fill is persisted with the
        # request, so recovery must see it after a restart.
        assert request.payload["input_data"]["ruleset_version"] == RULESET_VERSION
        if len(review_calls) == 1:
            raise ProviderError("review timeout", evidence="uncertain", error_code="LLM_TIMEOUT")
        return _review(
            target_id,
            [
                _payload(target_id, "G03_CONFIRMATION"),
                _payload(target_id, "G05_EXECUTION_BOUNDARY"),
                _payload(target_id, "advisory:aesthetics", outcome="fail"),
                _payload(target_id, "advisory:lighting", outcome="unknown"),
                _payload(target_id, "advisory:creative"),
                _payload(target_id, "advisory:composition", outcome="not_applicable"),
            ],
        )

    async with environment(full_settings, engineer=engineer, reviewer=reviewer) as (
        app,
        client,
        _llm,
        visual,
        _registry,
    ):
        setup = await setup_confirmed_project(app, client)
        failed = await workflow_action(client, setup, "compile_prompt")
        assert failed.status_code == 503, failed.text
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        prompt = next(item for item in view["artifacts"] if item["kind"] == "prompt_plan")
        assert prompt["status"] == "candidate"
        assert engineer_calls == [1]
        assert review_calls == [1]

        continued = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S08"
        assert recovered["status"] == "active"
        approved = next(item for item in recovered["artifacts"] if item["kind"] == "prompt_plan")
        assert approved["id"] == prompt["id"]
        assert approved["status"] == "approved"
        assert engineer_calls == [1]
        assert review_calls == [1, 1]
        assert visual.submit_calls == []
