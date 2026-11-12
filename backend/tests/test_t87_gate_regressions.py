from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.workflow.stages import s09
from app.workflow.tables import WorkflowRun
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import _drain, _environment, _rows, _setup_s06
from tests.test_t66_replay_drift import _generation_engineer_responder
from tests.test_v2_t08_visual_final_brief import (
    Script,
    _act,
    _artifact,
    _create_project,
    _seed_upstream,
    ready,
    visual_data,
)
from tests.test_v2_t08_visual_final_brief import (
    _environment as visual_environment,
)
from tests.test_v2_t40_prompt_review import (
    base_brief,
    candidate_data,
    engineer_response,
    environment,
    review_response,
    setup_confirmed_project,
    workflow_action,
)


@pytest.mark.asyncio
async def test_s07_rejects_all_not_applicable_prompt_review(full_settings):
    """WG3-AC-01：S07 两个必审规则全部 N/A 且无证据时，不得批准提示词。"""
    brief = base_brief()
    data = candidate_data(brief)

    def reviewer(request):
        response = review_response(request.payload["review_targets"][0]["target_id"])
        for check in response["data"]["results"][0]["checks"]:
            check["outcome"] = "not_applicable"
            check["evidence_ids"] = []
        return response

    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=reviewer,
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_REVIEW_INVALID"
        assert visual.submit_calls == []


class _AdvisoryFailureScript(Script):
    """当前目录的 G03_CONFIRMATION 建议失败；未知规则仍由 #90 回归拒绝。"""

    def __call__(self, request):
        if request.payload["task"]["role"] == "output_reviewer":
            target = request.payload["review_targets"][0]
            target_id = target["target_id"]
            evidence = list(request.payload["task"]["allowed_evidence_ids"])[:1] or [
                target_id
            ]
            checks = [
                {
                    "rule_id": rule_id,
                    "outcome": "pass",
                    "evidence_ids": evidence,
                    "note": "",
                }
                for rule_id in request.payload["task"]["required_rule_ids"]
            ]
            checks.append(
                {
                    "rule_id": "G03_CONFIRMATION",
                    "outcome": "fail",
                    "evidence_ids": evidence,
                    "note": "建议性治理检查未通过",
                }
            )
            return ready(
                "output_reviewer",
                {
                    "results": [
                        {
                            "target_id": target_id,
                            "decision": "pass",
                            "checks": checks,
                        }
                    ]
                },
            )
        return super().__call__(request)


@pytest.mark.asyncio
async def test_visual_gate_ignores_advisory_failure_when_blocking_checks_pass(full_settings):
    """WG3-AC-02：所有必审项通过时，注册的额外建议失败不得推翻批准。"""
    script = _AdvisoryFailureScript(visual=[visual_data()])
    async with visual_environment(full_settings, script) as (application, client, _llm, _visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        plan = _artifact(response.json(), "visual_plan")
        assert plan is not None and plan["status"] == "approved"


@pytest.mark.asyncio
async def test_prompt_coverage_format_error_keeps_original_intent(full_settings):
    """WG3-AC-08：覆盖映射格式错误不得放弃原意图或要求修改需求。"""
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(
        full_settings,
        llm,
        visual,
        responder=_generation_engineer_responder(fake=True),
    ) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert response.status_code == 200, response.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status != "abandoned"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)
        async with app.state.session_factory() as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
        assert run is not None
        assert run.issue_code != "PROMPT_REQUIRES_RECONFIRMATION"
        assert visual.submit_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rule_id", ["hard:creative", "hard:style", "hard:lighting"])
async def test_soft_visual_preferences_are_not_hard_prompt_coverage_requirements(
    full_settings, rule_id: str
):
    """WG3-AC-04：一般风格、氛围、光线偏好不得作为硬性提示词覆盖门槛。"""
    brief = base_brief()
    data = candidate_data(brief, omit=rule_id)
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"]
        ),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        assert visual.submit_calls == []


def test_image_composition_rule_does_not_merge_soft_preferences():
    """WG3-AC-04：图片 composition 规则不得把一般创意、风格、光线并入硬性要求。"""
    requirement = s09._composition_requirement(
        {
            "composition": "主体居中，留白充足",
            "creative_direction": "清晨窗边自然光，简洁温暖",
            "style_keywords": ["极简", "自然"],
            "lighting": "柔和侧光",
        },
        "fallback",
    )
    assert requirement == "构图：主体居中，留白充足"


@pytest.mark.asyncio
async def test_final_card_preserves_dedicated_visual_fields(full_settings):
    """WG3-AC-06：背景、色板、文字布局、参考处理必须进入最终执行快照。"""
    visual = visual_data()
    script = Script(visual=[visual], reviews=["pass", "pass"])
    async with visual_environment(full_settings, script) as (application, client, _llm, _visual):
        project = await _create_project(client)
        await _seed_upstream(application, project["id"])
        response = await _act(client, project["id"], "plan_visual")
        assert response.status_code == 200, response.text
        response = await _act(client, project["id"], "finalize_requirements")
        assert response.status_code == 200, response.text
        card = _artifact(response.json(), "final_brief")
        assert card is not None and card["payload"] is not None
        payload = card["payload"]
        assert payload.get("background") == visual["background"]
        assert payload.get("palette") == visual["palette"]
        assert payload.get("text_layout") == visual["text_layout"]
        assert payload.get("reference_treatment") == visual["reference_treatment"]


@pytest.mark.asyncio
async def test_multi_sentence_exact_copy_with_newline_is_covered(full_settings):
    """WG3-AC-09：分号、句号或换行原样出现不得被判为漏覆盖。"""
    brief = base_brief()
    brief["purpose"] = "电商主图\n用于大屏展示"
    data = candidate_data(brief)
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"]
        ),
    ) as (_app, client, _llm, _visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 200, response.text
        prompt = next(
            artifact
            for artifact in response.json()["artifacts"]
            if artifact["kind"] == "prompt_plan"
        )
        assert prompt["status"] == "approved"


@pytest.mark.asyncio
async def test_negation_flip_is_not_covered(full_settings):
    """WG3-AC-09：否定翻转不得因字符子序列匹配而假通过。"""
    brief = base_brief()
    brief["must_preserve"] = ["杯身无图案，保持白色"]
    data = candidate_data(brief)
    data["positive_prompt"] = data["positive_prompt"].replace("杯身无图案", "杯身有图案")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"]
        ),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_cross_clause_character_stitching_is_not_covered(full_settings):
    """WG3-AC-09：不得从互不相关的句子中拼出已确认要求。"""
    brief = base_brief()
    brief["must_include"] = ["杯身蓝色鲸鱼图案；用于大屏展示"]
    data = candidate_data(brief)
    data["positive_prompt"] = data["positive_prompt"].replace(
        "杯身蓝色鲸鱼图案；用于大屏展示", "杯身蓝色鲸鱼图案用于品牌宣传；大屏展示"
    )
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"]
        ),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_numeric_quantity_change_is_not_false_passed(full_settings):
    """WG3-AC-09：数值或数量变化不得因插入字符被字符子序列算法忽略。"""
    brief = base_brief()
    data = candidate_data(brief)
    data["positive_prompt"] = data["positive_prompt"].replace("350 毫升", "3500 毫升")
    async with environment(
        full_settings,
        engineer=lambda _request: engineer_response(data),
        reviewer=lambda request: review_response(
            request.payload["review_targets"][0]["target_id"]
        ),
    ) as (_app, client, _llm, visual, _registry):
        setup = await setup_confirmed_project(_app, client, brief_override=brief)
        response = await workflow_action(client, setup, "compile_prompt")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "PROMPT_INVALID"
        assert visual.submit_calls == []
