from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.main import create_app
from app.workflow import state
from app.workflow.stages import s05
from app.workflow.tables import (
    Artifact,
    ArtifactReview,
    GenerationJob,
    RevisionBudget,
    RoleCall,
)
from tests.conftest import migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client


def brief_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "product_description": "白色陶瓷马克杯，350ml，杯身无图案",
        "purpose": "电商详情页主图",
        "facts": [
            {
                "text": "容量为 350ml",
                "basis": "user_declared",
                "evidence_ids": ["user-evidence-1"],
            }
        ],
        "creative_direction": None,
        "style_keywords": ["极简"],
        "composition": None,
        "lighting": None,
        "texts": [],
        "must_include": ["白色陶瓷马克杯"],
        "must_preserve": ["杯身无图案，保持白色"],
        "forbidden": ["不得出现人物"],
        "size": "1024x1024",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": [],
        "unresolved": [],
    }
    data.update(overrides)
    return data


def creative_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "direction": "柔和晨光下的极简桌面场景，突出杯身轮廓",
        "selling_points": [],
        "copy_lines": [],
    }
    data.update(overrides)
    return data


def visual_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "style_keywords": ["极简", "留白"],
        "composition": "马克杯居中偏右，主体占画面约六成，左下留出呼吸空间",
        "lighting": "左前方柔和晨光，杯口有轻微高光",
        "background": "浅灰米色无缝桌面与背景",
        "palette": ["白色", "浅灰", "暖米色"],
        "text_layout": "画面无画面文字",
        "reference_treatment": "无参考图，按已确认需求独立构图",
        "preservation_rules": [
            "白色陶瓷马克杯",
            "杯身无图案，保持白色",
            "不得出现人物",
        ],
    }
    data.update(overrides)
    return data


def reference_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "reference_usage": "style",
        "observations": [
            {
                "kind": "composition",
                "description": "参考图采用左重右轻的三角构图，留白较多",
                "confidence": "clear",
                "evidence_ids": ["ref-evidence-1"],
            }
        ],
        "texts": [],
        "uncertainties": [],
    }
    data.update(overrides)
    return data


def ready(role: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "status": "ready",
        "data": data,
        "issues": [],
        "questions": [],
    }


class Script:
    """Deterministic external model adapter script for this ticket only."""

    def __init__(
        self,
        visual: list[Any] | None = None,
        reviews: list[Any] | None = None,
    ) -> None:
        self.visual = list(visual or [])
        self.reviews = list(reviews or [])

    def __call__(self, request: Any) -> dict[str, Any]:
        task = request.payload["task"]
        role = task["role"]
        if role == "visual_director":
            item = self.visual.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, tuple) and item[0] == "needs_input":
                _, code, detail = item
                evidence = task["allowed_evidence_ids"]
                return {
                    "role": role,
                    "status": "needs_input",
                    "data": None,
                    "issues": [
                        {
                            "code": code,
                            "path": "data",
                            "detail": detail,
                            "evidence_ids": evidence[:1],
                        }
                    ],
                    "questions": [],
                }
            return ready(role, copy.deepcopy(item))
        if role == "output_reviewer":
            item = self.reviews.pop(0)
            target = request.payload["review_targets"][0]
            target_id = target["target_id"]
            rules = list(task["required_rule_ids"])
            evidence = list(task["allowed_evidence_ids"])[:1] or [target_id]
            if isinstance(item, str):
                decision, mode = item, "full"
            else:
                decision = item.get("decision", "pass")
                mode = item.get("mode", "full")
            outcome = (
                "pass" if decision == "pass" else ("unknown" if decision == "unknown" else "fail")
            )
            checks = [
                {
                    "rule_id": rule_id,
                    "outcome": outcome,
                    "evidence_ids": evidence,
                    "note": "",
                }
                for rule_id in rules
            ]
            if mode == "missing":
                checks = checks[:-1]
            elif mode == "duplicate":
                checks = checks + [copy.deepcopy(checks[0])]
            elif mode == "foreign_evidence":
                for check in checks:
                    check["evidence_ids"] = ["foreign-evidence"]
            elif mode == "extra_rule":
                checks.append(
                    {
                        "rule_id": "S05_EXTRA_RULE",
                        "outcome": "pass",
                        "evidence_ids": evidence,
                        "note": "",
                    }
                )
            elif mode == "advisories":
                checks.extend(
                    [
                        {
                            "rule_id": rule_id,
                            "outcome": outcome,
                            "evidence_ids": check_evidence,
                            "note": "",
                        }
                        for rule_id, check_evidence, outcome in (
                            ("G07_SCOPE_ROLE", evidence, "pass"),
                            ("G07_FACT_SOURCE", evidence, "pass"),
                            ("G07_UNKNOWN_AS_FACT", [], "pass"),
                            ("G03_REQUIREMENT_CONSISTENCY", evidence, "pass"),
                            ("G04_PROHIBITED", evidence, "pass"),
                            ("G07_OUTPUT_STRUCTURE", evidence, "pass"),
                            ("G06_EVIDENCE_COVERAGE", evidence, "pass"),
                            ("G07_UNAUTHORIZED_ACTION", [], "pass"),
                        )
                    ]
                )
            elif mode == "url":
                checks[0]["note"] = "参见 https://evil.invalid/report"
            return {
                "role": role,
                "status": "ready",
                "data": {
                    "results": [
                        {
                            "target_id": target_id,
                            "decision": decision,
                            "checks": checks,
                        }
                    ]
                },
                "issues": [],
                "questions": [],
            }
        raise AssertionError(f"unexpected role {role!r}")


@asynccontextmanager
async def _environment(settings: Any, script: Script):
    llm = LLMStub(role_responder=script)
    visual = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, llm, visual


async def _create_project(client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_upstream(
    application: Any,
    project_id: str,
    *,
    brief: dict[str, Any] | None = None,
    creative: dict[str, Any] | None = None,
    reference: dict[str, Any] | None = None,
) -> None:
    """Prepare real approved upstream artifacts (allowed by the ticket)."""
    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx)
    ctx = await state.reload_context(ctx)
    if reference is not None:
        report = await state.start_artifact(ctx, "reference_report", reference)
        await state.add_review(
            ctx,
            report,
            reviewer_role="output_reviewer",
            decision="pass",
            checks=[{"rule_id": "G02_REPORT", "outcome": "pass", "evidence_ids": [], "note": ""}],
        )
    for kind, payload in (
        ("brief_draft", brief or brief_data()),
        ("creative_direction", creative or creative_data()),
    ):
        artifact = await state.start_artifact(ctx, kind, payload)
        await state.add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision="pass",
            checks=[
                {
                    "rule_id": f"{kind.upper()}_PASS",
                    "outcome": "pass",
                    "evidence_ids": [],
                    "note": "",
                }
            ],
        )
    await state.set_stage(ctx, "S05", "active")


async def _act(
    client: AsyncClient,
    project_id: str,
    action: str,
    *,
    expected_revision: int = 0,
    text: str | None = None,
    payload: dict[str, Any] | None = None,
):
    body: dict[str, Any] = {"expected_revision": expected_revision}
    if text is not None:
        body["text"] = text
    if payload is not None:
        body["payload"] = payload
    return await client.post(f"/api/projects/{project_id}/workflow/actions/{action}", json=body)


async def _view(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _artifact(view: dict[str, Any], kind: str, *, latest: bool = True) -> dict[str, Any] | None:
    matches = [item for item in view["artifacts"] if item["kind"] == kind]
    if not matches:
        return None
    return matches[-1] if latest else matches[0]


async def _counts(application: Any, project_id: str) -> tuple[int, int, int]:
    async with application.state.session_factory() as session:
        artifacts = (
            await session.scalars(select(Artifact).where(Artifact.project_id == project_id))
        ).all()
        jobs = (
            await session.scalars(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
            )
        ).all()
        calls = (
            await session.scalars(select(RoleCall).where(RoleCall.project_id == project_id))
        ).all()
    return len(artifacts), len(jobs), len(calls)


async def test_visual_plan_and_final_brief_are_published_only_after_independent_review(
    full_settings,
):
    """AC-14/AC-13：无文字需求先出文字视觉方案，再合成并独立审查最终卡片。"""
    script = Script(visual=[visual_data()], reviews=["pass", "pass"])
    async with _environment(full_settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])

        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S05"
        assert view["status"] == "active"
        plan = _artifact(view, "visual_plan")
        assert plan is not None
        assert plan["status"] == "approved"
        assert plan["payload"] == visual_data()
        assert [action["id"] for action in view["available_actions"]] == [
            "revise_visual",
            "finalize_requirements",
        ]

        # The producer and reviewer are separate calls; the reviewer gets the
        # target artifact and its approved dependencies.
        assert [call.payload["task"]["role"] for call in llm.role_calls_received] == [
            "visual_director",
            "output_reviewer",
        ]
        producer = llm.role_calls_received[0]
        reviewer = llm.role_calls_received[1]
        assert producer.payload["approved_inputs"]["brief_draft"]["product_description"]
        assert producer.payload["approved_inputs"]["creative_direction"]["direction"]
        assert reviewer.payload["review_targets"][0]["target_kind"] == "visual_plan"
        assert reviewer.payload["review_targets"][0]["target_id"] == plan["id"]
        assert reviewer.payload["task"]["required_rule_ids"] == list(s05.REQUIRED_VISUAL_RULES)

        response = await _act(client, project["id"], "finalize_requirements")
        assert response.status_code == 200, response.text
        final_view = response.json()
        assert final_view["stage"] == "S06"
        assert final_view["status"] == "active"
        card = _artifact(final_view, "final_brief")
        assert card is not None
        assert card["status"] == "approved"
        assert card["payload"] is not None
        payload = card["payload"]
        assert payload["product_description"] == brief_data()["product_description"]
        assert payload["purpose"] == brief_data()["purpose"]
        assert payload["creative_direction"] == creative_data()["direction"]
        assert payload["style_keywords"] == visual_data()["style_keywords"]
        assert payload["composition"] == visual_data()["composition"]
        assert payload["lighting"] == visual_data()["lighting"]
        assert payload["texts"] == []
        assert payload["must_include"] == brief_data()["must_include"]
        assert set(brief_data()["must_preserve"]) <= set(payload["must_preserve"])
        assert payload["forbidden"] == brief_data()["forbidden"]
        assert payload["size"] == "1024x1024"
        assert payload["reference_usage"] == "none"
        assert len(card["digest"]) == 64
        assert card["id"]

        # The card itself is a separate review target, not an extension of the
        # visual-plan review call.
        card_reviewer = llm.role_calls_received[-1]
        assert card_reviewer.payload["task"]["role"] == "output_reviewer"
        assert card_reviewer.payload["review_targets"][0]["target_kind"] == "final_brief"
        assert card_reviewer.payload["review_targets"][0]["target_id"] == card["id"]
        assert card_reviewer.payload["task"]["required_rule_ids"] == list(s05.REQUIRED_CARD_RULES)
        async with application.state.session_factory() as session:
            reviews = (
                await session.scalars(
                    select(ArtifactReview).where(ArtifactReview.artifact_id == card["id"])
                )
            ).all()
        assert len(reviews) == 1
        assert reviews[0].reviewer_role == "output_reviewer"
        assert reviews[0].decision == "pass"
        import json

        raw = json.loads(reviews[0].raw_json or "{}")
        assert {item["kind"] for item in raw["dependencies"]} == {
            "brief_draft",
            "creative_direction",
            "visual_plan",
        }
        assert visual.submit_calls == []
        assert (await _counts(application, project["id"]))[1] == 0


async def test_final_card_survives_restart_and_reads_from_public_detail(full_settings):
    """AC-14：最终卡片身份、摘要、revision 与内容持久化后可再次读取。"""
    script = Script(visual=[visual_data()], reviews=["pass", "pass"])
    async with _environment(full_settings, script) as (application, client, _, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        planned = await _act(client, project["id"], "plan_visual")
        assert planned.status_code == 200, planned.text
        finalized = await _act(client, project["id"], "finalize_requirements")
        assert finalized.status_code == 200, finalized.text
        card = _artifact(finalized.json(), "final_brief")
        assert card["status"] == "approved"
        card_id = card["id"]
        card_digest = card["digest"]
        card_payload = card["payload"]

    # Restart a real application against the same temporary SQLite and files.
    application = create_app(settings=full_settings, registry=build_test_registry())
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            view = await _view(client, project["id"])
    assert view["stage"] == "S06"
    restored = _artifact(view, "final_brief")
    assert restored["id"] == card_id
    assert restored["digest"] == card_digest
    assert restored["payload"] == card_payload
    assert restored["status"] == "approved"


async def test_visual_plan_must_match_requested_text_or_state_no_text(full_settings):
    """AC-13：无文字必须明确无画面文字；有文字必须只安排已确认文案。"""
    script = Script(
        visual=[
            visual_data(
                text_layout="画面无画面文字，不添加任何标题",
            ),
            visual_data(
                text_layout="画面无画面文字，另加「买一送一」",
            ),
        ],
        reviews=["pass"],
    )
    async with _environment(full_settings, script) as (application, client, _, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])

        first = await _act(client, project["id"], "plan_visual")
        assert first.status_code == 200, first.text
        assert _artifact(first.json(), "visual_plan")["status"] == "approved"

        # Build a new run/project for the rejected copy case so the first
        # approved plan does not short-circuit the second attempt.
        rejected_project = await _create_project(client)
        await _seed_upstream(application, rejected_project["id"])
        second = await _act(client, rejected_project["id"], "plan_visual")
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "REQUIREMENT_DRIFT"
        view = await _view(client, rejected_project["id"])
        assert _artifact(view, "visual_plan") is None
        assert visual.submit_calls == []


async def test_visual_plan_no_text_may_quote_the_requirement(full_settings):
    """无文字时，引用用户“不需要文字”的原话只是需求说明，不是新增画面文案。"""
    script = Script(
        visual=[
            visual_data(
                text_layout=(
                    "无画面文字。按用户“不需要文字”的要求，不安排标题、副标题、"
                    "卖点、价格、标识、水印或任何可读字形；不预留文字框。"
                )
            )
        ],
        reviews=["pass"],
    )
    async with _environment(full_settings, script) as (application, client, _, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])

        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        assert _artifact(response.json(), "visual_plan")["status"] == "approved"


async def test_text_requirement_cannot_gain_unconfirmed_copy(full_settings):
    """AC-13：视觉方案只能布局已确认准确文字，不能新增文案。"""
    script = Script(
        visual=[
            visual_data(
                text_layout="左上角用同一字体呈现「限时特惠」，右下角加「买一送一」",
                preservation_rules=[
                    "白色陶瓷马克杯",
                    "杯身无图案，保持白色",
                    "不得出现人物",
                ],
            )
        ]
    )
    async with _environment(full_settings, script) as (application, client, _, _):
        project = await _create_project(client)
        await _seed_upstream(
            application,
            project["id"],
            brief=brief_data(texts=["限时特惠"]),
        )
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REQUIREMENT_DRIFT"


async def test_visual_needs_input_returns_to_requirements_without_artifact(full_settings):
    """AC-14：视觉导演不能解决的需求问题回到需求顾问，不生成候选。"""
    script = Script(
        visual=[("needs_input", "REQUIREMENT_DRIFT", "参考用途与硬要求冲突，需确认")],
        reviews=[],
    )
    async with _environment(full_settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "REQUIREMENT_DRIFT"
        assert _artifact(view, "visual_plan") is None
        assert [call.payload["task"]["role"] for call in llm.role_calls_received] == [
            "visual_director"
        ]
        assert visual.submit_calls == []


async def test_review_revise_keeps_candidate_isolated_and_revision_uses_budget(full_settings):
    """AC-14/AC-16：未批候选不显示，用户修订形成新候选并重新审查。"""
    revised = visual_data(
        composition="马克杯居中，背景更简洁，主体占画面约六成",
        text_layout="画面无画面文字",
    )
    script = Script(
        visual=[visual_data(), revised],
        reviews=["revise", "pass"],
    )
    async with _environment(full_settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])

        first = await _act(client, project["id"], "plan_visual")
        assert first.status_code == 200, first.text
        view = first.json()
        candidate = _artifact(view, "visual_plan")
        assert candidate["status"] == "candidate"
        assert candidate["payload"] is None
        assert [action["id"] for action in view["available_actions"]] == ["revise_visual"]

        second = await _act(
            client,
            project["id"],
            "revise_visual",
            text="请让背景更简洁，主体更居中",
        )
        assert second.status_code == 200, second.text
        view = second.json()
        assert view["stage"] == "S05"
        revised_artifact = _artifact(view, "visual_plan")
        assert revised_artifact["status"] == "approved"
        assert revised_artifact["revision_no"] == 2
        assert revised_artifact["payload"]["composition"] == revised["composition"]
        assert len(llm.role_calls_received) == 4
        assert [call.payload["task"]["role"] for call in llm.role_calls_received] == [
            "visual_director",
            "output_reviewer",
            "visual_director",
            "output_reviewer",
        ]
        assert llm.role_calls_received[2].payload["repair_codes"] == list(s05.REQUIRED_VISUAL_RULES)
        async with application.state.session_factory() as session:
            budget = await session.get(RevisionBudget, (view["run_id"], "visual_plan"))
        assert budget is not None and budget.used == 2
        assert visual.submit_calls == []


async def test_review_unknown_blocks_and_unapproved_card_is_not_visible(full_settings):
    """AC-17/AC-18：审查 unknown 不放行，候选卡片不通过公共详情泄露。"""
    script = Script(visual=[visual_data()], reviews=["unknown"])
    async with _environment(full_settings, script) as (application, client, _, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        view = response.json()
        plan = _artifact(view, "visual_plan")
        assert plan["status"] == "candidate"
        assert plan["payload"] is None
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_UNKNOWN"
        assert visual.submit_calls == []


async def test_review_missing_duplicate_foreign_checks_never_approve(full_settings):
    """AC-18：schema 通过不等于语义通过；缺项、重复、外来证据都不放行。"""
    for mode in ("missing", "duplicate", "foreign_evidence", "extra_rule", "url"):
        script = Script(visual=[visual_data()], reviews=[{"decision": "pass", "mode": mode}])
        async with _environment(full_settings, script) as (application, client, _, _):
            project = await _create_project(client)
            await _seed_upstream(application, project["id"])
            response = await _act(client, project["id"], "plan_visual")
            assert response.status_code == 409, (mode, response.text)
            assert response.json()["error"]["code"] == "REVIEW_INVALID"
            view = await _view(client, project["id"])
            assert view["issue"]["code"] == "REVIEW_INVALID", mode
            plan = _artifact(view, "visual_plan")
            assert plan is None or plan["status"] != "approved", mode


async def test_review_accepts_advisory_governance_checks(full_settings):
    """共享 reviewer 的额外治理检查不应阻断视觉方案批准。"""
    script = Script(visual=[visual_data()], reviews=[{"decision": "pass", "mode": "advisories"}])
    async with _environment(full_settings, script) as (application, client, _, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        view = await _view(client, project["id"])
        plan = _artifact(view, "visual_plan")
        assert plan is not None and plan["status"] == "approved"


async def test_visual_technical_failure_has_zero_automatic_retries(full_settings):
    """AC-35：技术失败只调用一次，不自动重试，也不产生候选。"""
    error = ProviderError("模型超时", evidence="not_submitted", error_code="LLM_TIMEOUT")
    script = Script(visual=[error, visual_data()], reviews=["pass"])
    async with _environment(full_settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert len(llm.role_calls_received) == 1
        view = await _view(client, project["id"])
        assert view["status"] == "failed"
        assert _artifact(view, "visual_plan") is None
        assert [action["id"] for action in view["available_actions"]] == ["continue_workflow"]

        # A manual retry is allowed; it is a user action, not an automatic replay.
        retry = await _act(client, project["id"], "plan_visual")
        assert retry.status_code == 200, retry.text
        assert len(llm.role_calls_received) == 3
        assert _artifact(retry.json(), "visual_plan")["status"] == "approved"
        assert visual.submit_calls == []


async def test_visual_budget_exhausts_after_three_candidates(full_settings):
    """AC-16：每个产物最多三个候选，预算持久化且“继续”不能重置。"""
    script = Script(
        visual=[visual_data(), visual_data(), visual_data(), visual_data()],
        reviews=["revise", "revise", "revise"],
    )
    async with _environment(full_settings, script) as (application, client, llm, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        first = await _act(client, project["id"], "plan_visual")
        assert first.status_code == 200, first.text
        second = await _act(client, project["id"], "revise_visual", text="第一次调整")
        assert second.status_code == 200, second.text
        third = await _act(client, project["id"], "revise_visual", text="第二次调整")
        assert third.status_code == 200, third.text
        fourth = await _act(client, project["id"], "revise_visual", text="第三次调整")
        assert fourth.status_code == 409, fourth.text
        assert fourth.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        assert len(llm.role_calls_received) == 6
        async with application.state.session_factory() as session:
            budget = await session.get(RevisionBudget, (first.json()["run_id"], "visual_plan"))
        assert budget is not None and budget.used == 3


async def test_finalize_requires_approved_dependencies(full_settings):
    """AC-14：最终卡片必须由三份已批准上游产物合成，缺一份即拒绝。"""
    script = Script(reviews=["pass"])
    async with _environment(full_settings, script) as (application, client, llm, _):
        project = await _create_project(client)
        ctx = await state.load_context(application, project["id"])
        await state.create_run(ctx)
        ctx = await state.reload_context(ctx)
        brief = await state.start_artifact(ctx, "brief_draft", brief_data())
        await state.add_review(
            ctx,
            brief,
            reviewer_role="output_reviewer",
            decision="pass",
            checks=[{"rule_id": "BRIEF_PASS", "outcome": "pass", "evidence_ids": [], "note": ""}],
        )
        await state.set_stage(ctx, "S05", "active")

        response = await _act(client, project["id"], "finalize_requirements")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "ARTIFACT_NOT_APPROVED"
        assert llm.role_calls_received == []


async def test_stale_visual_dependency_does_not_publish_final_card(full_settings):
    """AC-24：视觉方案依赖已变化时，旧方案不能合成或发布当前卡片。"""
    script = Script(visual=[visual_data()], reviews=["pass"])
    async with _environment(full_settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        planned = await _act(client, project["id"], "plan_visual")
        assert planned.status_code == 200, planned.text

        # A newer approved brief makes the approved visual plan stale.
        ctx = await state.load_context(application, project["id"])
        newer = await state.start_artifact(
            ctx,
            "brief_draft",
            brief_data(product_description="白色陶瓷马克杯，350ml，杯身带金色边线"),
        )
        await state.add_review(
            ctx,
            newer,
            reviewer_role="output_reviewer",
            decision="pass",
            checks=[{"rule_id": "BRIEF_PASS", "outcome": "pass", "evidence_ids": [], "note": ""}],
        )

        response = await _act(client, project["id"], "finalize_requirements")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "RUN_SUPERSEDED"
        view = await _view(client, project["id"])
        assert _artifact(view, "final_brief") is None
        assert visual.submit_calls == []


async def test_old_expected_revision_is_rejected_before_model_call(full_settings):
    """AC-21：旧 revision 请求在路由层被拒绝，不调用任何模型。"""
    script = Script(visual=[visual_data()], reviews=["pass"])
    async with _environment(full_settings, script) as (application, client, llm, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual", expected_revision=7)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"
        assert llm.role_calls_received == []


async def test_action_payloads_reject_extra_fields(full_settings):
    """AC-15：动作参数关闭额外字段，非法请求不触达模型。"""
    script = Script(visual=[visual_data()], reviews=["pass"])
    async with _environment(full_settings, script) as (application, client, llm, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual", payload={"unexpected": "x"})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert llm.role_calls_received == []

        planned = await _act(client, project["id"], "plan_visual")
        assert planned.status_code == 200, planned.text
        revise = await _act(
            client,
            project["id"],
            "revise_visual",
            text="调整构图",
            payload={"unexpected": "x"},
        )
        assert revise.status_code == 422, revise.text
        finalize = await _act(
            client,
            project["id"],
            "finalize_requirements",
            payload={"unexpected": "x"},
        )
        assert finalize.status_code == 422, finalize.text
        assert len(llm.role_calls_received) == 2


async def test_product_and_composition_reference_boundaries(full_settings):
    """AC-28：product 只保留可确定商品外观，composition 只借布局。"""
    cases = [
        (
            "product",
            "参考图仅用于确认商品外观与结构，保持商品身份与数量",
            reference_data(reference_usage="product"),
        ),
        (
            "composition",
            "只借参考图构图与布局，商品外观按已确认需求保持",
            reference_data(reference_usage="composition"),
        ),
    ]
    for usage, treatment, report in cases:
        script = Script(
            visual=[visual_data(reference_treatment=treatment)],
            reviews=["pass"],
        )
        async with _environment(full_settings, script) as (application, client, _, _):
            project = await _create_project(client)
            await _seed_upstream(
                application,
                project["id"],
                brief=brief_data(
                    reference_asset_id="ref-asset-1",
                    reference_usage=usage,
                    facts=[],
                ),
                reference=report,
            )
            response = await _act(client, project["id"], "plan_visual")
            assert response.status_code == 200, (usage, response.text)
            assert _artifact(response.json(), "visual_plan")["status"] == "approved"


async def test_contract_extra_field_on_visual_output_is_rejected(full_settings):
    """AC-15/AC-47：schema 额外字段在业务前失败，不持久化候选。"""
    bad = visual_data()
    bad["extra_field"] = "not allowed"
    script = Script(visual=[bad], reviews=[])
    async with _environment(full_settings, script) as (application, client, llm, _):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        view = await _view(client, project["id"])
        assert _artifact(view, "visual_plan") is None
        async with application.state.session_factory() as session:
            calls = (
                await session.scalars(select(RoleCall).where(RoleCall.project_id == project["id"]))
            ).all()
        assert len(calls) == 1 and calls[0].status == "invalid"


async def test_final_card_rejects_unresolved_or_unsourced_facts(full_settings):
    """AC-14：必要信息空、未解决阻塞或来源无效时不得进入待确认。"""
    cases = [
        (brief_data(unresolved=["价格待确认"]), "MISSING_INPUT"),
        (brief_data(product_description=None), "MISSING_INPUT"),
        (
            brief_data(
                facts=[
                    {
                        "text": "杯身带金色边线",
                        "basis": "image_observed",
                        "evidence_ids": ["ref-evidence-1"],
                    }
                ]
            ),
            "MISSING_EVIDENCE",
        ),
    ]
    for brief, expected in cases:
        script = Script(visual=[visual_data()], reviews=["pass"])
        async with _environment(full_settings, script) as (application, client, _, _):
            project = await _create_project(client)
            await _seed_upstream(application, project["id"], brief=brief)
            planned = await _act(client, project["id"], "plan_visual")
            # The visual plan itself may still be produced; the card must fail.
            assert planned.status_code in (200, 409), planned.text
            response = await _act(client, project["id"], "finalize_requirements")
            assert response.status_code == 409, (expected, response.text)
            assert response.json()["error"]["code"] == expected
            view = await _view(client, project["id"])
            assert _artifact(view, "final_brief") is None


async def test_reference_usage_boundary_is_enforced(full_settings):
    """AC-28：style 参考只能借风格，不能改为商品身份或外观来源。"""
    script = Script(
        visual=[
            visual_data(reference_treatment="沿用参考图的风格与色彩，不改变商品外观"),
            visual_data(reference_treatment="把参考图的商品外观直接替换商品"),
        ],
        reviews=["pass"],
    )
    async with _environment(full_settings, script) as (application, client, _, _):
        project = await _create_project(client)
        await _seed_upstream(
            application,
            project["id"],
            brief=brief_data(
                reference_asset_id="ref-asset-1",
                reference_usage="style",
                facts=[],
            ),
            reference=reference_data(),
        )
        first = await _act(client, project["id"], "plan_visual")
        assert first.status_code == 200, first.text
        assert _artifact(first.json(), "visual_plan")["status"] == "approved"

        rejected_project = await _create_project(client)
        await _seed_upstream(
            application,
            rejected_project["id"],
            brief=brief_data(
                reference_asset_id="ref-asset-1",
                reference_usage="style",
                facts=[],
            ),
            reference=reference_data(),
        )
        second = await _act(client, rejected_project["id"], "plan_visual")
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "REQUIREMENT_DRIFT"


async def test_visual_and_final_reviews_are_skipped_when_gates_are_off(full_settings):
    settings = full_settings.model_copy(update={"workflow_gate_mode": "requirements_only"})
    script = Script(visual=[visual_data()], reviews=[])
    async with _environment(settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])

        planned = await _act(client, project["id"], "plan_visual")
        assert planned.status_code == 200, planned.text
        plan = _artifact(planned.json(), "visual_plan")
        assert plan is not None
        assert plan["status"] == "approved"

        finalized = await _act(client, project["id"], "finalize_requirements")
        assert finalized.status_code == 200, finalized.text
        final_view = finalized.json()
        assert final_view["stage"] == "S06"
        assert final_view["status"] == "active"
        card = _artifact(final_view, "final_brief")
        assert card is not None
        assert card["status"] == "approved"

        assert [call.payload["task"]["role"] for call in llm.role_calls_received] == [
            "visual_director"
        ]
        assert visual.submit_calls == []


async def test_approved_visual_plan_stays_resumable_from_loaded_context(full_settings):
    """Approved S05 must expose finalize_requirements from the context snapshot.

    A stale or locked side connection used to hide the approved artifact and
    fall back to plan_visual, which left the stage stuck after a refresh.
    """
    script = Script(visual=[visual_data()], reviews=["pass", "pass"])
    async with _environment(full_settings, script) as (application, client, llm, visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])

        planned = await _act(client, project["id"], "plan_visual")
        assert planned.status_code == 200, planned.text

        ctx = await state.load_context(application, project["id"])
        assert ctx.latest_artifact_statuses["visual_plan"] == "approved"
        assert s05.available(ctx) == ["revise_visual", "finalize_requirements"]

        view = await _view(client, project["id"])
        assert [action["id"] for action in view["available_actions"]] == [
            "revise_visual",
            "finalize_requirements",
        ]

        refreshed = await _view(client, project["id"])
        assert [action["id"] for action in refreshed["available_actions"]] == [
            "revise_visual",
            "finalize_requirements",
        ]

        before = await _counts(application, project["id"])
        calls_before = len(llm.role_calls_received)
        repeated = await _act(client, project["id"], "plan_visual")
        assert repeated.status_code == 200, repeated.text
        assert await _counts(application, project["id"]) == before
        assert len(llm.role_calls_received) == calls_before
        assert [action["id"] for action in repeated.json()["available_actions"]] == [
            "revise_visual",
            "finalize_requirements",
        ]

        finalized = await _act(client, project["id"], "finalize_requirements")
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["stage"] == "S06"
        assert finalized.json()["status"] == "active"
