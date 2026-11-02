from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.main import create_app
from app.workflow import state
from app.workflow.adjudication import RULESET_VERSION, ProtocolIssue, Rule, RuleSet, review_output
from app.workflow.stages import s04, s05
from app.workflow.tables import ArtifactReview, RoleCall
from tests.conftest import migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client

TARGET = "artifact:creative"
_PASS_CHECKS = {
    "checks": [{"rule_id": "G03_BRIEF", "outcome": "pass", "evidence_ids": [], "note": ""}]
}


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


def _output(target_id: str, checks: list[dict[str, Any]], decision: str = "pass") -> dict[str, Any]:
    return {"results": [{"target_id": target_id, "decision": decision, "checks": checks}]}


def _review(
    ruleset: Any,
    checks: list[dict[str, Any]],
    *,
    target_id: str = TARGET,
    decision: str = "pass",
) -> Any:
    return review_output(
        _output(target_id, checks, decision),
        target_id=target_id,
        ruleset=ruleset,
        allowed_evidence={target_id},
    )


# --- catalogue semantics -----------------------------------------------------


def test_s04_catalogue_registers_program_rules_as_blocking() -> None:
    assert s04.RULESET.target_kind == "creative_direction"
    assert [rule.rule_id for rule in s04.RULESET.blocking()] == list(s04.REQUIRED_RULES)
    advisory = {rule.rule_id for rule in s04.RULESET.advisory()}
    assert "G03_CONFIRMATION" in advisory
    assert "review:quality_report" in advisory
    assert not set(s04.REQUIRED_RULES) & advisory


def test_s05_catalogue_is_selected_by_review_target() -> None:
    visual = s05._ruleset_for(s05.REQUIRED_VISUAL_RULES)
    card = s05._ruleset_for(s05.REQUIRED_CARD_RULES)
    assert visual.target_kind == "visual_plan"
    assert card.target_kind == "final_card"
    assert {rule.rule_id for rule in visual.blocking()} == set(s05.REQUIRED_VISUAL_RULES)
    assert {rule.rule_id for rule in card.blocking()} == set(s05.REQUIRED_CARD_RULES)
    assert not set(s05.REQUIRED_VISUAL_RULES) & set(s05.REQUIRED_CARD_RULES)


def test_blocking_rule_cannot_be_waived_as_not_applicable() -> None:
    checks = [_check(rule_id, outcome="not_applicable") for rule_id in s04.REQUIRED_RULES]
    with pytest.raises(ProtocolIssue) as info:
        _review(s04.RULESET, checks)
    assert info.value.code == "CONTRADICTION"


def test_advisory_unknown_never_changes_a_satisfied_gate() -> None:
    checks = [_check(rule_id) for rule_id in s04.REQUIRED_RULES]
    checks.append(_check("G03_CONFIRMATION", outcome="unknown", evidence=()))
    result = _review(s04.RULESET, checks)
    assert result.approved is True
    assert result.advisory == {"G03_CONFIRMATION": "unknown"}


def test_advisory_pass_cannot_rescue_a_blocking_failure() -> None:
    checks = [_check(rule_id) for rule_id in s04.REQUIRED_RULES]
    checks[0] = _check(s04.REQUIRED_RULES[0], outcome="fail")
    checks.append(_check("G03_CONFIRMATION", outcome="pass", evidence=()))
    result = _review(s04.RULESET, checks, decision="revise")
    assert result.approved is False
    assert result.blocking_failures == (s04.REQUIRED_RULES[0],)


def test_rule_outside_the_catalogue_is_a_protocol_issue() -> None:
    checks = [_check(rule_id) for rule_id in s04.REQUIRED_RULES]
    checks.append(_check("S04_EXTRA_RULE"))
    with pytest.raises(ProtocolIssue) as info:
        _review(s04.RULESET, checks)
    assert info.value.code == "UNKNOWN_RULE"


def test_duplicate_rule_instance_is_a_protocol_issue() -> None:
    checks = [_check(rule_id) for rule_id in s04.REQUIRED_RULES]
    checks.append(copy.deepcopy(checks[0]))
    with pytest.raises(ProtocolIssue) as info:
        _review(s04.RULESET, checks)
    assert info.value.code == "DUPLICATE_INSTANCE"


def test_rule_instances_are_checked_individually() -> None:
    ruleset = RuleSet(
        "visual_plan",
        (
            Rule("S05_TEXT_LAYOUT", "文字层 1", instance="line:1"),
            Rule("S05_TEXT_LAYOUT", "文字层 2", instance="line:2"),
        ),
        version=RULESET_VERSION,
    )
    checks = [
        _check("S05_TEXT_LAYOUT", evidence=(TARGET,), instance="line:1"),
        _check("S05_TEXT_LAYOUT", evidence=(TARGET,), instance="line:2"),
    ]
    result = _review(ruleset, checks)
    assert result.approved is True
    with pytest.raises(ProtocolIssue) as info:
        _review(ruleset, checks[:1])
    assert info.value.code == "MISSING_RESULT"
    assert result.ruleset_version == RULESET_VERSION


# --- HTTP entry points -------------------------------------------------------


def brief_payload() -> dict[str, Any]:
    return {
        "product_description": "用户提供的陶瓷杯",
        "purpose": "电商商品主图",
        "facts": [
            {
                "text": "用户声明商品为陶瓷杯",
                "basis": "user_declared",
                "evidence_ids": ["user:1"],
            }
        ],
        "creative_direction": "以单个杯子为主体展示外观",
        "style_keywords": ["清爽"],
        "composition": "居中呈现一只杯子",
        "lighting": "柔光",
        "texts": [],
        "must_include": ["一只杯子"],
        "must_preserve": ["参考图中的杯形"],
        "forbidden": ["额外文字"],
        "size": "1024x1024",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": [],
        "unresolved": [],
    }


def creative_payload() -> dict[str, Any]:
    return {
        "direction": "简洁背景突出杯子外观",
        "selling_points": [],
        "copy_lines": [],
    }


def visual_payload() -> dict[str, Any]:
    return {
        "style_keywords": ["清爽", "留白"],
        "composition": "杯子居中，主体占画面六成",
        "lighting": "柔和侧光",
        "background": "浅灰无缝背景",
        "palette": ["白色", "浅灰"],
        "text_layout": "画面无文字",
        "reference_treatment": "无参考图，按确认需求构图",
        "preservation_rules": ["一只杯子"],
    }


def _ready(role: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"role": role, "status": "ready", "data": data, "issues": [], "questions": []}


class Script:
    """Deterministic external model adapter for the T04 acceptance cases."""

    def __init__(
        self,
        *,
        creative: list[Any] | None = None,
        visual: list[Any] | None = None,
        reviews: list[Any] | None = None,
    ) -> None:
        self.creative = list(creative or [])
        self.visual = list(visual or [])
        self.reviews = list(reviews or [])

    def __call__(self, request: Any) -> dict[str, Any]:
        task = request.payload["task"]
        role = str(task["role"])
        if role == "creative_planner":
            return _ready(role, copy.deepcopy(self.creative.pop(0)))
        if role == "visual_director":
            return _ready(role, copy.deepcopy(self.visual.pop(0)))
        if role != "output_reviewer":
            raise AssertionError(f"unexpected role {role!r}")
        item = self.reviews.pop(0)
        if isinstance(item, BaseException):
            raise item
        mode = str(item)
        target_id = str(request.payload["review_targets"][0]["target_id"])
        rules = list(task["required_rule_ids"])
        evidence = list(task["allowed_evidence_ids"])[:1] or [target_id]
        checks = [
            {"rule_id": rule_id, "outcome": "pass", "evidence_ids": evidence, "note": ""}
            for rule_id in rules
        ]
        if mode == "missing":
            checks = checks[:-1]
        elif mode == "duplicate":
            checks.append(copy.deepcopy(checks[0]))
        elif mode == "not_applicable":
            for check in checks:
                check["outcome"] = "not_applicable"
                check["evidence_ids"] = []
        elif mode == "unknown_rule":
            checks.append(
                {
                    "rule_id": "S05_EXTRA_RULE",
                    "outcome": "pass",
                    "evidence_ids": evidence,
                    "note": "",
                }
            )
        elif mode == "advisory_fail":
            checks.append(
                {
                    "rule_id": "G03_CONFIRMATION",
                    "outcome": "fail",
                    "evidence_ids": [],
                    "note": "",
                }
            )
        return {
            "role": role,
            "status": "ready",
            "data": {
                "results": [{"target_id": target_id, "decision": "pass", "checks": checks}]
            },
            "issues": [],
            "questions": [],
        }


@asynccontextmanager
async def _environment(settings: Any, script: Script):
    llm = LLMStub(role_responder=script)
    visual_stub = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual_stub)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with (
        application.router.lifespan_context(application),
        authenticated_client(application) as client,
    ):
        yield application, client, llm, visual_stub


async def _create_project(client: AsyncClient) -> str:
    response = await client.post("/api/projects", json={})
    assert response.status_code in {200, 201}, response.text
    return response.json()["id"]


async def _seed_brief(application: Any, project_id: str) -> str:
    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx, "S04")
    ctx = await state.reload_context(ctx)
    artifact = await state.start_artifact(ctx, "brief_draft", brief_payload())
    await state.add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks=_PASS_CHECKS,
    )
    return ctx.run_id


async def _seed_visual_inputs(application: Any, project_id: str) -> None:
    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx, "S05")
    ctx = await state.reload_context(ctx)
    for kind, payload in (
        ("brief_draft", brief_payload()),
        ("creative_direction", creative_payload()),
    ):
        artifact = await state.start_artifact(ctx, kind, payload)
        await state.add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision="pass",
            checks=_PASS_CHECKS,
        )
    await state.set_stage(ctx, "S05", "active")


async def _act(client: AsyncClient, project_id: str, action: str, *, expected_revision: int = 0):
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/{action}",
        json={"expected_revision": expected_revision},
    )


async def _view(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _role_calls(application: Any, run_id: str) -> list[RoleCall]:
    async with application.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(RoleCall)
                    .where(RoleCall.run_id == run_id)
                    .order_by(RoleCall.started_at.asc())
                )
            ).all()
        )


@pytest.mark.parametrize("mode", ["missing", "duplicate", "not_applicable", "unknown_rule"])
async def test_s04_protocol_defects_never_approve(llm_settings, mode: str) -> None:
    script = Script(creative=[creative_payload()], reviews=[mode])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        run_id = await _seed_brief(application, project_id)

        response = await _act(client, project_id, "plan_creative")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        view = await _view(client, project_id)
        assert view["stage"] == "S04"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_INVALID"
        creative = [item for item in view["artifacts"] if item["kind"] == "creative_direction"]
        assert len(creative) == 1
        assert creative[0]["status"] == "candidate"
        assert creative[0]["payload"] is None
        assert [action["id"] for action in view["available_actions"]] == ["continue_workflow"]
        assert visual.submit_calls == []
        calls = await _role_calls(application, run_id)
        assert [call.role for call in calls] == ["creative_planner", "output_reviewer"]


async def test_s04_advisory_failure_does_not_block(llm_settings) -> None:
    script = Script(creative=[creative_payload()], reviews=["advisory_fail"])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        await _seed_brief(application, project_id)

        response = await _act(client, project_id, "plan_creative")

        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S05"
        assert view["status"] == "active"
        creative = [item for item in view["artifacts"] if item["kind"] == "creative_direction"]
        assert creative[0]["status"] == "approved"
        assert visual.submit_calls == []
        reviewer_request = stub.role_calls_received[-1]
        assert reviewer_request.payload["input_data"]["ruleset_version"] == RULESET_VERSION
        assert isinstance(reviewer_request.payload["input_data"]["rules"], list)
        async with application.state.session_factory() as session:
            reviews = list(
                (
                    await session.scalars(
                        select(ArtifactReview).where(
                            ArtifactReview.artifact_id == creative[0]["id"]
                        )
                    )
                ).all()
            )
        assert reviews
        assert json.loads(reviews[-1].checks_json)["ruleset_version"] == RULESET_VERSION


async def test_s04_recovery_replays_the_same_creative_candidate(llm_settings) -> None:
    timeout = ProviderError("review timeout", evidence="uncertain", error_code="LLM_TIMEOUT")
    script = Script(creative=[creative_payload()], reviews=[timeout, "pass"])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        run_id = await _seed_brief(application, project_id)

        failed = await _act(client, project_id, "plan_creative")
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        view = await _view(client, project_id)
        assert view["status"] == "blocked"
        assert "continue_workflow" in [action["id"] for action in view["available_actions"]]
        creative = [item for item in view["artifacts"] if item["kind"] == "creative_direction"]
        assert len(creative) == 1 and creative[0]["status"] == "candidate"

        resumed = await _act(client, project_id, "continue_workflow")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["stage"] == "S05"
        approved = [
            item for item in resumed.json()["artifacts"] if item["kind"] == "creative_direction"
        ]
        assert len(approved) == 1
        assert approved[0]["id"] == creative[0]["id"]
        assert approved[0]["status"] == "approved"
        calls = await _role_calls(application, run_id)
        assert [call.role for call in calls] == [
            "creative_planner",
            "output_reviewer",
            "output_reviewer",
        ]
        assert visual.submit_calls == []


@pytest.mark.parametrize("mode", ["missing", "duplicate", "unknown_rule", "not_applicable"])
async def test_s05_protocol_defects_never_approve(llm_settings, mode: str) -> None:
    script = Script(visual=[visual_payload()], reviews=[mode])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        await _seed_visual_inputs(application, project_id)

        response = await _act(client, project_id, "plan_visual")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        view = await _view(client, project_id)
        assert view["stage"] == "S05"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_INVALID"
        plan = [item for item in view["artifacts"] if item["kind"] == "visual_plan"]
        assert len(plan) == 1 and plan[0]["status"] == "candidate"
        assert plan[0]["payload"] is None
        assert visual.submit_calls == []


async def test_s05_advisory_failure_does_not_block(llm_settings) -> None:
    script = Script(visual=[visual_payload()], reviews=["advisory_fail"])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        await _seed_visual_inputs(application, project_id)

        response = await _act(client, project_id, "plan_visual")

        assert response.status_code == 200, response.text
        plan = [item for item in response.json()["artifacts"] if item["kind"] == "visual_plan"]
        assert plan[0]["status"] == "approved"
        assert visual.submit_calls == []


async def test_s06_card_protocol_defect_keeps_card_unconfirmed(llm_settings) -> None:
    script = Script(visual=[visual_payload()], reviews=["pass", "missing"])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        await _seed_visual_inputs(application, project_id)

        planned = await _act(client, project_id, "plan_visual")
        assert planned.status_code == 200, planned.text
        response = await _act(client, project_id, "finalize_requirements")

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        view = await _view(client, project_id)
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_INVALID"
        card = [item for item in view["artifacts"] if item["kind"] == "final_brief"]
        assert len(card) == 1 and card[0]["status"] == "candidate"
        assert card[0]["payload"] is None
        assert visual.submit_calls == []


async def test_s05_recovery_replays_the_same_visual_candidate(llm_settings) -> None:
    timeout = ProviderError("review timeout", evidence="uncertain", error_code="LLM_TIMEOUT")
    script = Script(visual=[visual_payload()], reviews=[timeout, "pass"])
    async with _environment(llm_settings, script) as (application, client, stub, visual):
        project_id = await _create_project(client)
        await _seed_visual_inputs(application, project_id)

        failed = await _act(client, project_id, "plan_visual")
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] in {"REVIEW_UNAVAILABLE", "SERVICE_UNAVAILABLE"}
        view = await _view(client, project_id)
        assert view["status"] == "blocked"
        assert "continue_workflow" in [action["id"] for action in view["available_actions"]]
        plan = [item for item in view["artifacts"] if item["kind"] == "visual_plan"]
        assert len(plan) == 1 and plan[0]["status"] == "candidate"

        resumed = await _act(client, project_id, "continue_workflow")
        assert resumed.status_code == 200, resumed.text
        approved = [item for item in resumed.json()["artifacts"] if item["kind"] == "visual_plan"]
        assert len(approved) == 1
        assert approved[0]["id"] == plan[0]["id"]
        assert approved[0]["status"] == "approved"
        assert visual.submit_calls == []
