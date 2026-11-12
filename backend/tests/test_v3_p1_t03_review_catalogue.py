from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient

from app.main import create_app
from app.workflow.adjudication import (
    ADVISORY_GOVERNANCE_RULES,
    RULESET_VERSION,
    ProtocolIssue,
    Rule,
    RuleSet,
    adjudicate,
    governance_ruleset,
    review_output,
)
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from tests.conftest import migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client

# WG3-AC-02/03: the server catalogue decides which rules block a release, what
# evidence each rule accepts and which reviewer findings are advisory. Reviewer
# output that leaves the catalogue is a typed protocol issue, never an outage and
# never an implicit approval.

TARGET = "artifact:brief"


def _ruleset(**overrides: Any) -> RuleSet:
    rules = (
        Rule("G03_STRUCTURE", "结构"),
        Rule("G03_TEXTS", "文字"),
        Rule("G03_EXTRA", "附加观察", kind="advisory"),
    )
    return RuleSet("brief_draft", rules, **overrides)


def _check(
    rule_id: str,
    *,
    outcome: str = "pass",
    evidence: tuple[str, ...] = (TARGET,),
    instance: str = "",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "outcome": outcome,
        "evidence_ids": list(evidence),
        "note": "",
        "instance": instance,
    }


def _adjudicate(
    checks: list[dict[str, Any]],
    *,
    decision: str = "pass",
    ruleset: RuleSet | None = None,
    allowed: tuple[str, ...] = (TARGET,),
    target: str = TARGET,
) -> Any:
    return adjudicate(
        ruleset or _ruleset(),
        target_id=target,
        result_target_id=target,
        declared_decision=decision,
        checks=checks,
        allowed_evidence=set(allowed),
    )


def test_governance_ruleset_owns_blocking_and_advisory_identities() -> None:
    ruleset = governance_ruleset(
        "requirement_draft", (("G03_STRUCTURE", "结构"), ("G03_TEXTS", "文字"))
    )
    blocking = [rule.rule_id for rule in ruleset.blocking()]
    advisory = [rule.rule_id for rule in ruleset.advisory()]
    assert blocking == ["G03_STRUCTURE", "G03_TEXTS"]
    assert set(advisory) == ADVISORY_GOVERNANCE_RULES - {"G03_STRUCTURE", "G03_TEXTS"}
    assert ruleset.version == RULESET_VERSION
    described = ruleset.describe()
    assert [item["rule_id"] for item in described][:2] == ["G03_STRUCTURE", "G03_TEXTS"]


@pytest.mark.parametrize("rule_id", ["G99_INVENTED", "g03_structure", "review:whatever"])
def test_rule_identities_outside_the_catalogue_are_protocol_issues(rule_id: str) -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate([_check("G03_STRUCTURE"), _check("G03_TEXTS"), _check(rule_id)])
    assert caught.value.code == "UNKNOWN_RULE"


def test_a_blocking_rule_may_not_be_waived_by_the_reviewer() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_STRUCTURE", outcome="not_applicable", evidence=()),
                _check("G03_TEXTS"),
            ]
        )
    assert caught.value.code == "CONTRADICTION"


def test_missing_blocking_result_is_a_protocol_issue() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate([_check("G03_STRUCTURE")])
    assert caught.value.code == "MISSING_RESULT"


def test_advisory_findings_never_change_a_satisfied_gate() -> None:
    for outcome in ("pass", "fail", "unknown", "not_applicable"):
        result = _adjudicate(
            [
                _check("G03_STRUCTURE"),
                _check("G03_TEXTS"),
                _check("G03_EXTRA", outcome=outcome, evidence=()),
            ]
        )
        assert result.decision == "pass"
        assert result.approved is True
        assert result.advisory["G03_EXTRA"] == outcome


def test_advisory_pass_cannot_rescue_a_blocking_failure() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_STRUCTURE", outcome="fail"),
                _check("G03_TEXTS"),
                _check("G03_EXTRA"),
            ]
        )
    assert caught.value.code == "CONTRADICTION"
    result = _adjudicate(
        [_check("G03_STRUCTURE", outcome="fail"), _check("G03_TEXTS")], decision="revise"
    )
    assert result.decision == "revise"
    assert result.approved is False
    assert result.blocking_failures == ("G03_STRUCTURE",)


def test_evidence_outside_the_rule_contract_is_rejected() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_STRUCTURE", evidence=("artifact:other",)),
                _check("G03_TEXTS"),
            ]
        )
    assert caught.value.code == "ILLEGAL_EVIDENCE"
    with pytest.raises(ProtocolIssue) as typed:
        _adjudicate(
            [
                _check("G03_STRUCTURE", evidence=("http://evil.invalid",)),
                _check("G03_TEXTS"),
            ]
        )
    assert typed.value.code == "ILLEGAL_EVIDENCE"


def test_duplicate_rule_instances_are_a_protocol_issue() -> None:
    with pytest.raises(ProtocolIssue) as caught:
        _adjudicate(
            [
                _check("G03_STRUCTURE"),
                _check("G03_STRUCTURE"),
                _check("G03_TEXTS"),
            ]
        )
    assert caught.value.code == "DUPLICATE_INSTANCE"


def test_server_declared_non_applicability_may_be_omitted() -> None:
    ruleset = RuleSet(
        "brief_draft",
        (
            Rule("G03_STRUCTURE", "结构"),
            Rule("G03_TEXTS", "文字", applicable=False, reason="本次需求没有文字"),
        ),
    )
    result = _adjudicate([_check("G03_STRUCTURE")], ruleset=ruleset)
    assert result.decision == "pass"
    with pytest.raises(ValueError):
        Rule("G03_TEXTS", "文字", applicable=False)


def test_review_output_must_carry_exactly_one_matching_result() -> None:
    ruleset = _ruleset()
    with pytest.raises(ProtocolIssue) as caught:
        review_output(
            {"results": []}, target_id=TARGET, ruleset=ruleset, allowed_evidence={TARGET}
        )
    assert caught.value.code == "MISSING_RESULT"
    with pytest.raises(ProtocolIssue) as wrong:
        review_output(
            {
                "results": [
                    {
                        "target_id": "artifact:other",
                        "decision": "pass",
                        "checks": [_check("G03_STRUCTURE")],
                    }
                ]
            },
            target_id=TARGET,
            ruleset=ruleset,
            allowed_evidence={TARGET},
        )
    assert wrong.value.code == "WRONG_TARGET"


# --- HTTP: the S03 reviewer contract defect is a typed, replayable block -------


def _brief(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": {
            "product_description": "米色托特包",
            "purpose": "电商商品主图",
            "facts": [
                {
                    "text": "用户声明商品为米色托特包",
                    "basis": "user_declared",
                    "evidence_ids": [evidence],
                }
            ],
            "creative_direction": None,
            "style_keywords": [],
            "composition": None,
            "lighting": None,
            "texts": [],
            "must_include": ["一只米色托特包"],
            "must_preserve": [],
            "forbidden": [],
            "size": request.payload["capabilities"]["size"] or "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": [],
            "unresolved": [],
        },
        "issues": [],
        "questions": [],
    }


def _review(target_id: str, rules: tuple[str, ...], *, decision: str = "pass") -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": decision,
                    "checks": [_check(rule_id, evidence=(target_id,)) for rule_id in rules],
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


class Script:
    """First requirements review omits six mandatory rules, the replay is valid."""

    def __init__(self) -> None:
        self.brief_calls = 0
        self.review_calls = 0

    def responder(self, request: Any) -> dict[str, Any]:
        task = request.payload["task"]
        role = task["role"]
        if role == "intent_router":
            evidence = task["allowed_evidence_ids"][0]
            return {
                "role": "intent_router",
                "status": "ready",
                "data": {
                    "route": "image_request",
                    "action": "new_request",
                    "evidence_ids": [evidence],
                },
                "issues": [],
                "questions": [],
            }
        if role == "output_reviewer":
            target = request.payload["review_targets"][0]
            target_id = target["target_id"]
            if target.get("target_kind") == "intent_route":
                return _review(target_id, ("G01_SCOPE", "G01_ACTION"))
            self.review_calls += 1
            if self.review_calls == 1:
                return _review(target_id, ("G03_STRUCTURE",))
            return _review(target_id, REVIEW_RULE_IDS)
        if role == "requirements_consultant":
            self.brief_calls += 1
            return _brief(request)
        raise AssertionError(f"unexpected role {role!r}")


@asynccontextmanager
async def _environment(
    settings: Any, script: Script
) -> AsyncIterator[tuple[Any, AsyncClient, LLMStub, VisualStub]]:
    stub = LLMStub(role_responder=script.responder)
    visual = VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with (
        application.router.lifespan_context(application),
        authenticated_client(application) as client,
    ):
        yield application, client, stub, visual


async def _act(
    client: AsyncClient,
    project_id: str,
    action: str,
    *,
    expected_revision: int,
    text: str | None = None,
) -> Any:
    body: dict[str, Any] = {"expected_revision": expected_revision}
    if text is not None:
        body["text"] = text
    return await client.post(f"/api/projects/{project_id}/workflow/actions/{action}", json=body)


async def _view(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def test_s03_contract_defect_blocks_then_replays_without_regenerating(full_settings):
    script = Script()
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = (await client.post("/api/projects", json={})).json()
        started = await _act(
            client, project["id"], "submit_request", expected_revision=0, text="做一张商品主图"
        )
        assert started.status_code == 200, started.text
        failed = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started.json()["project_revision"],
        )
        assert failed.status_code == 409, failed.text
        assert failed.json()["error"]["code"] == "REVIEW_INVALID"
        assert script.brief_calls == 1
        assert script.review_calls == 1
        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_INVALID"
        assert [action["id"] for action in view["available_actions"]] == ["continue_workflow"]
        drafts = [item for item in view["artifacts"] if item["kind"] == "brief_draft"]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "candidate"
        assert drafts[0]["payload"] is None
        assert view["approvals"] == []

        resumed = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=view["project_revision"],
        )
        assert resumed.status_code == 200, resumed.text
        after = resumed.json()
        assert after["stage"] == "S04"
        assert after["status"] == "active"
        assert script.brief_calls == 1
        assert script.review_calls == 2
        assert [item["id"] for item in after["artifacts"] if item["kind"] == "brief_draft"] == [
            drafts[0]["id"]
        ]
        assert visual.submit_calls == []


async def test_s03_rejection_does_not_offer_continue_but_a_defect_does(full_settings):
    class Rejecting(Script):
        def responder(self, request: Any) -> dict[str, Any]:
            task = request.payload["task"]
            if task["role"] == "output_reviewer":
                target = request.payload["review_targets"][0]
                if target.get("target_kind") == "brief_draft":
                    self.review_calls += 1
                    data = _review(target["target_id"], REVIEW_RULE_IDS, decision="reject")
                    data["data"]["results"][0]["checks"][0]["outcome"] = "fail"
                    return data
            return super().responder(request)

    script = Rejecting()
    async with _environment(full_settings, script) as (application, client, _stub, _visual):
        project = (await client.post("/api/projects", json={})).json()
        started = await _act(
            client, project["id"], "submit_request", expected_revision=0, text="做一张商品主图"
        )
        assert started.status_code == 200, started.text
        rejected = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started.json()["project_revision"],
        )
        assert rejected.status_code == 200, rejected.text
        view = await _view(client, project["id"])
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_REJECTED"
        assert "continue_workflow" not in [action["id"] for action in view["available_actions"]]
