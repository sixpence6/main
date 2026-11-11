from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.main import create_app
from app.workflow import templates
from app.workflow.contracts import ContractError, validate_role_output
from app.workflow.roles import _normalize_envelope_placement
from app.workflow.stages.s03 import REVIEW_RULE_IDS, _alignment_report
from app.workflow.tables import Artifact, GenerationJob, RoleCall
from tests.conftest import migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client

# AC-12/AC-13/AC-14/AC-15/AC-16/AC-17: the requirements consultant is a strict,
# independently reviewed v2.0.0 role. These tests exercise the public HTTP path,
# a real temporary SQLite database and the real workflow tables; only the model
# adapters are scripted.


def _ready_data(request: Any, **overrides: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    data: dict[str, Any] = {
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
        "assumptions": ["建议使用简洁背景"],
        "unresolved": [],
    }
    data.update(overrides)
    return data


def _ready(request: Any, **overrides: Any) -> dict[str, Any]:
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": _ready_data(request, **overrides),
        "issues": [],
        "questions": [],
    }


def _ready_no_product(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": {
            "product_description": None,
            "purpose": "活动海报",
            "facts": [
                {
                    "text": "画面不含商品，只呈现人物写真。",
                    "basis": "user_declared",
                    "evidence_ids": [evidence],
                }
            ],
            "creative_direction": None,
            "style_keywords": [],
            "composition": None,
            "lighting": None,
            "texts": [],
            "must_include": ["人物写真"],
            "must_preserve": ["画面只呈现人物写真", "画面不含商品", "画面不含任何文案"],
            "forbidden": ["任何商品", "任何文案或文字"],
            "size": request.payload["capabilities"]["size"] or "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": [],
            "unresolved": [],
        },
        "issues": [],
        "questions": [],
    }


def _needs_input(
    request: Any,
    *,
    question: str,
    path: str,
    detail: str,
    code: str = "MISSING_INPUT",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "role": "requirements_consultant",
        "status": "needs_input",
        "data": data,
        "issues": [
            {
                "code": code,
                "path": path,
                "detail": detail,
                "evidence_ids": [],
            }
        ],
        "questions": [question],
    }


def _review_checks(
    target_id: str,
    mode: str,
    *,
    rules: tuple[str, ...] = REVIEW_RULE_IDS,
) -> list[dict[str, Any]]:
    extras: tuple[str, ...] = ()
    if mode == "extra":
        extras = (
            "G03_CONFIRMATION_STATE",
            "G03_EVIDENCE_COVERAGE",
            "G03_AUTH_BOUNDARY",
        )
    elif mode == "advisory_na":
        extras = ("G03_CONFIRMATION_STATE",)
    selected = (*rules, *extras) if mode != "missing" else rules[:1]
    checks: list[dict[str, Any]] = []
    for index, rule_id in enumerate(selected):
        evidence = ["foreign-evidence"] if mode == "foreign_evidence" else [target_id]
        outcome = "pass"
        if mode == "revise" and index == 0:
            outcome = "fail"
        if mode == "unknown" and index == 0:
            outcome = "unknown"
        if mode == "reject" and index == 0:
            outcome = "fail"
        if mode == "empty_required_evidence" and index == 0:
            evidence = []
        if mode == "advisory_na" and rule_id not in rules:
            outcome = "not_applicable"
            evidence = []
        checks.append(
            {
                "rule_id": rule_id,
                "outcome": outcome,
                "evidence_ids": evidence,
                "note": "https://evil.invalid/report" if mode == "url" else "",
            }
        )
    return checks


def _review_response(
    target_id: str, mode: str, *, rules: tuple[str, ...] = REVIEW_RULE_IDS
) -> dict[str, Any]:
    decision = mode if mode in {"revise", "reject", "unknown"} else "pass"
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": decision,
                    "checks": _review_checks(target_id, mode, rules=rules),
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


class Script:
    def __init__(
        self,
        briefs: list[Any],
        *,
        review_modes: list[str] | None = None,
    ) -> None:
        self.briefs = list(briefs)
        self.review_modes = list(review_modes or ["pass"])
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
                return _review_response(target_id, "pass", rules=("G01_SCOPE", "G01_ACTION"))
            mode = self.review_modes[min(self.review_calls, len(self.review_modes) - 1)]
            self.review_calls += 1
            if mode == "provider_error":
                raise ProviderError(
                    "provider down",
                    evidence="uncertain",
                    error_code="LLM_UNAVAILABLE",
                )
            return _review_response(target_id, mode)
        if role == "requirements_consultant":
            item = self.briefs[min(self.brief_calls, len(self.briefs) - 1)]
            self.brief_calls += 1
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                return item(request)
            return item
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
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, stub, visual


async def _create_project(client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


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


async def _start(client: AsyncClient, project_id: str, text: str) -> dict[str, Any]:
    response = await _act(client, project_id, "submit_request", text=text, expected_revision=0)
    assert response.status_code == 200, response.text
    return response.json()


async def _view(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _messages(client: AsyncClient, project_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


async def _table_counts(application: Any, project_id: str) -> tuple[int, int]:
    async with application.state.session_factory() as session:
        artifacts = (
            await session.scalars(select(Artifact).where(Artifact.project_id == project_id))
        ).all()
        jobs = (
            await session.scalars(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
            )
        ).all()
    return len(artifacts), len(jobs)


def _valid_ready_payload(target_id: str = "user:1") -> dict[str, Any]:
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": {
            "product_description": "陶瓷杯",
            "purpose": "商品主图",
            "facts": [
                {
                    "text": "用户声明商品为陶瓷杯",
                    "basis": "user_declared",
                    "evidence_ids": [target_id],
                }
            ],
            "creative_direction": None,
            "style_keywords": [],
            "composition": None,
            "lighting": None,
            "texts": [],
            "must_include": [],
            "must_preserve": [],
            "forbidden": [],
            "size": "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": [],
            "unresolved": [],
        },
        "issues": [],
        "questions": [],
    }


def test_requirements_contract_accepts_only_legal_state_combinations():
    valid = _valid_ready_payload()
    assert validate_role_output("requirements_consultant", valid).status == "ready"

    needs_input = {
        "role": "requirements_consultant",
        "status": "needs_input",
        "data": None,
        "issues": [
            {
                "code": "MISSING_INPUT",
                "path": "product_description",
                "detail": "缺少商品主体",
                "evidence_ids": [],
            }
        ],
        "questions": ["商品主体是什么？"],
    }
    assert validate_role_output("requirements_consultant", needs_input).status == "needs_input"

    illegal = [
        {**valid, "questions": ["不应出现在 ready"]},
        {**valid, "status": "blocked", "data": valid["data"], "issues": valid["issues"]},
        {**needs_input, "questions": []},
        {**valid, "role": "intent_router"},
        {**valid, "extra": True},
    ]
    for payload in illegal:
        with pytest.raises(ContractError):
            validate_role_output("requirements_consultant", payload)


def test_role_normalizer_drops_unknown_top_level_fields():
    payload = {**_valid_ready_payload(), "questions_note": None}
    normalized = _normalize_envelope_placement(payload)
    assert "questions_note" not in normalized
    assert validate_role_output("requirements_consultant", normalized).status == "ready"


async def test_complete_text_request_creates_reviewed_draft_without_generation(full_settings):
    text = "请做一张米色托特包的商品主图，用于电商详情页。"
    script = Script([_ready])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], text)
        assert started["stage"] == "S03"
        assert started["status"] == "active"
        assert [action["id"] for action in started["available_actions"]] == ["compose_brief"]

        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"
        assert view["requirement_revision"] == 1
        assert view["approvals"] == []
        assert view["generation_job"] is None

        drafts = [item for item in view["artifacts"] if item["kind"] == "brief_draft"]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "approved"
        payload = drafts[0]["payload"]
        assert payload["product_description"] == "米色托特包"
        assert payload["purpose"] == "电商商品主图"
        assert payload["texts"] == []
        assert payload["size"] == "1024x1024"
        assert payload["facts"][0]["basis"] == "user_declared"
        assert payload["must_include"] == ["一只米色托特包"]
        assert payload["must_preserve"] == []
        assert payload["forbidden"] == []
        assert payload["assumptions"] == ["建议使用简洁背景"]
        assert payload["unresolved"] == []

        messages = await _messages(client, project["id"])
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[0]["content"] == text
        report = messages[1]["content"]
        assert report.startswith("需求对齐情况")
        assert "基本信息：3/3 项已明确" in report
        assert "商品主体：米色托特包" in report
        assert "使用场景与目标：电商商品主图" in report
        assert "输出尺寸：1024x1024" in report
        assert "还缺少的信息\n- 无，需求信息已对齐。" in report
        assert "确认需求后将生成图片提示词" in report
        assert "只有你确认提示词后，系统才会生成图片" in report
        producer = stub.role_calls_received[2]
        assert producer.payload["task"]["role"] == "requirements_consultant"
        assert producer.payload["input_data"]["user_text"] == text
        assert (
            producer.payload["input_data"]["user_messages"][0]["message_id"] == (messages[0]["id"])
        )
        assert messages[0]["id"] in producer.payload["task"]["allowed_evidence_ids"]
        assert producer.payload["approved_inputs"] == []
        reviewer = stub.role_calls_received[3]
        assert reviewer.payload["review_targets"][0]["target_kind"] == "brief_draft"
        assert reviewer.payload["review_targets"][0]["data"]["product_description"] == (
            "米色托特包"
        )
        assert text not in reviewer.system_prompt

        async with application.state.session_factory() as session:
            calls = (await session.scalars(select(RoleCall))).all()
        assert [call.role for call in calls] == [
            "intent_router",
            "output_reviewer",
            "requirements_consultant",
            "output_reviewer",
        ]
        assert all(call.attempt == 1 for call in calls)
        assert all(call.status == "ok" for call in calls)

        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 1
        assert jobs == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    ("kind", "question", "path"),
    [
        ("subject", "请说明要展示的商品主体是什么？", "product_description"),
        ("purpose", "这张图用于什么场景或用途？", "purpose"),
    ],
)
async def test_missing_subject_or_purpose_is_a_central_question(
    full_settings, kind, question, path
):
    if kind == "subject":
        data = _ready_data_for_questions(product_description=None)
    else:
        data = _ready_data_for_questions(purpose=None)
    first = _needs_input(
        None,  # replaced by lambda below
        question=question,
        path=path,
        detail="缺少必要信息",
        data=data,
    )
    script = Script([lambda request: first, _ready])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "帮我做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "MISSING_INPUT"
        assert view["issue"]["template_id"] is None
        assert view["issue"]["message"] == question
        assert [item["id"] for item in view["available_actions"]] == [
            "answer_requirements_clarification"
        ]
        assert view["artifacts"] == []

        roles = [request.payload["task"]["role"] for request in stub.role_calls_received]
        assert roles[-1] == "requirements_consultant"
        consultant_prompt = stub.role_calls_received[-1].system_prompt
        assert "只有确实缺少阻塞事实时才 needs_input" in consultant_prompt
        assert "商品主体 → 使用场景与目标 → 必须保留的商品特征" in consultant_prompt
        assert "给出 2—3 个贴合当前商品的示例选项" in consultant_prompt
        assert "不得重问已确认答案" in consultant_prompt

        answered = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="是一只米色托特包，用于电商商品主图。",
            expected_revision=view["project_revision"],
        )
        assert answered.status_code == 200, answered.text
        final = answered.json()
        assert final["stage"] == "S04"
        assert final["status"] == "active"
        assert final["requirement_revision"] == 2
        approved = [a for a in final["artifacts"] if a["status"] == "approved"]
        assert approved and approved[0]["payload"]["product_description"] == "米色托特包"

        messages = await _messages(client, project["id"])
        assert [message["role"] for message in messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert messages[1]["content"].startswith("需求对齐情况")
        assert "请补充以下信息" in messages[1]["content"]
        assert question in messages[1]["content"]
        assert "请按编号用自然语言回答" in messages[1]["content"]
        assert "回答过的问题不会重复询问" in messages[1]["content"]
        assert "推荐提示词" not in messages[1]["content"]
        assert messages[2]["content"] == "是一只米色托特包，用于电商商品主图。"
        assert "基本信息：3/3 项已明确" in messages[3]["content"]
        assert "还缺少的信息\n- 无，需求信息已对齐。" in messages[3]["content"]
        _, jobs = await _table_counts(application, project["id"])
        assert jobs == 0
        assert visual.submit_calls == []


async def test_option_only_reply_uses_prior_clarification_and_advances(full_settings):
    question = "请选择商品主体：A 米色托特包；B 黑色托特包。"
    data = _ready_data_for_questions(product_description=None)
    script = Script(
        [
            lambda request: _needs_input(
                request,
                question=question,
                path="product_description",
                detail="缺少商品主体",
                data=data,
            ),
            _ready,
        ]
    )
    async with _environment(full_settings, script) as (_, client, stub, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "needs_input"

        answered = await client.post(
            f"/api/projects/{project['id']}/messages",
            json={
                "content": "A",
                "expected_revision": view["project_revision"],
                "request_id": "11111111-1111-4111-8111-111111111111",
            },
        )
        assert answered.status_code == 200, answered.text
        final = answered.json()["workflow"]
        assert final["stage"] == "S04"
        assert final["status"] == "active"

        consultant_calls = [
            request.payload
            for request in stub.role_calls_received
            if request.payload["task"]["role"] == "requirements_consultant"
        ]
        assert len(consultant_calls) == 2
        second_input = consultant_calls[1]["input_data"]
        assert second_input["user_text"].endswith("A")
        assert question in second_input["pending_clarification"]
        conversation = second_input["conversation"]
        assert [item["role"] for item in conversation] == ["user", "assistant", "user"]
        assert question in conversation[1]["text"]
        assert conversation[-1]["text"] == "A"
        roles = [request.payload["task"]["role"] for request in stub.role_calls_received]
        assert "conversation_router" not in roles


def test_clarification_reply_example_matches_all_questions():
    report = _alignment_report(
        None,
        questions=["第一题", "第二题", "第三题", "第四题"],
        size="1024x1024",
        reference_asset_id=None,
        reference_usage="none",
        ready=False,
    )
    assert "例如「1A" not in report
    assert "请按编号用自然语言回答" in report
    assert "可以复制这一行开始：1? 2? 3? 4?" in report


async def test_numbered_answers_stay_on_their_own_questions(full_settings):
    first_questions = [
        "请选择钢笔风格。例如：A. 极简棚拍；B. 深色静物；C. 另行描述。",
        "请说明必须保留的钢笔特征。例如：A. 笔尖清晰；B. 有品牌铭文；C. 无特殊标识，笔身细节可自由表现。",
        "价格位置怎么处理。例如：A. 只留空白；B. 写具体价格；C. 写促销标语（请提供文案）。",
    ]
    first = _needs_input(
        None,
        question=first_questions[0],
        path="style_keywords",
        detail="缺少风格、保留特征和价格处理方式",
        data=_ready_data_for_questions(
            unresolved=["风格未确认", "必须保留特征未确认", "画面文字未确认"]
        ),
    )
    first["questions"] = first_questions
    second = lambda request: _needs_input(  # noqa: E731
        request,
        question="请提供促销标语原文，画面文字只能使用你给出的原文。",
        path="texts",
        detail="促销标语原文未提供",
        data=_ready_data_for_questions(
            style_keywords=["极简棚拍"],
            must_preserve=[],
            texts=[],
            unresolved=["促销标语原文未提供"],
        ),
    )
    script = Script([lambda request: first, second])
    async with _environment(full_settings, script) as (_, client, stub, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张钢笔促销主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "needs_input"
        assert "1A 2A 3A" in (await _messages(client, project["id"]))[1]["content"]

        answered = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1A 2C 3C",
            expected_revision=view["project_revision"],
        )
        assert answered.status_code == 200, answered.text
        answered_view = answered.json()
        assert answered_view["status"] == "needs_input"
        assert "必须保留" not in answered_view["issue"]["message"]
        assert "促销标语" in answered_view["issue"]["message"]

        consultant_calls = [
            request.payload
            for request in stub.role_calls_received
            if request.payload["task"]["role"] == "requirements_consultant"
        ]
        parsed = consultant_calls[1]["input_data"]["clarification_answers"]
        assert [item["number"] for item in parsed] == [1, 2, 3]
        assert parsed[0]["topics"] == ["visual"]
        assert parsed[1]["topics"] == ["preserve"]
        assert parsed[2]["topics"] == ["price"]
        assert parsed[1]["answer"].startswith("无特殊标识")
        assert parsed[2]["answer"].startswith("写促销标语")

        messages = await _messages(client, project["id"])
        assert "必须保留的商品特征：无（已确认）" in messages[-1]["content"]
        assert "请说明必须保留" not in messages[-1]["content"]
        assert "请提供促销标语原文" in messages[-1]["content"]


async def test_earlier_negative_answers_are_not_asked_again(full_settings):
    first_questions = [
        "请选择钢笔风格。例如：A. 极简棚拍；B. 深色静物；C. 另行描述。",
        "尺寸怎么处理。例如：A. 按 3840x2160；B. 严格 1:1；C. 其他比例。",
        "请说明必须保留的钢笔特征。例如：A. 笔尖清晰；B. 有品牌铭文；C. 无特殊标识，笔身细节可自由表现。",
        "价格位置怎么处理。例如：A. 只留空白；B. 写具体价格；C. 写促销标语（请提供文案）。",
    ]
    second_questions = [
        "请说明必须保留的钢笔特征。例如：A. 没有必须保留项；B. 有必须保留的特征，请直接说明。",
        "请提供促销标语原文。例如：A. 直接给出原文；B. 只写不含数字的短句（请给出原文）；C. 改为不出现文字。",
    ]
    third_questions = [
        "请说明必须保留的钢笔特征。例如：A. 没有必须保留项；B. 有必须保留的特征，请直接说明。",
        "请提供促销标语原文，画面文字只能使用你给出的原文。",
    ]
    first = _needs_input(
        None,
        question=first_questions[0],
        path="style_keywords",
        detail="缺少风格、尺寸、保留特征和价格处理方式",
        data=_ready_data_for_questions(
            unresolved=["风格未确认", "尺寸未确认", "必须保留特征未确认", "画面文字未确认"]
        ),
    )
    first["questions"] = first_questions
    second = _needs_input(
        None,
        question=second_questions[0],
        path="must_preserve",
        detail="必须保留特征未确认",
        data=_ready_data_for_questions(
            style_keywords=["极简棚拍"], unresolved=["画面文字未确认"]
        ),
    )
    second["questions"] = second_questions
    third = _needs_input(
        None,
        question=third_questions[0],
        path="must_preserve",
        detail="必须保留特征未确认",
        data=_ready_data_for_questions(
            style_keywords=["极简棚拍"], unresolved=["画面文字未确认"]
        ),
    )
    third["questions"] = third_questions
    script = Script([lambda request: first, lambda request: second, lambda request: third])
    async with _environment(full_settings, script) as (_, client, stub, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张钢笔促销主图")
        first_response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert first_response.status_code == 200, first_response.text
        first_view = first_response.json()

        second_response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1A 2B 3C",
            expected_revision=first_view["project_revision"],
        )
        assert second_response.status_code == 200, second_response.text
        second_view = second_response.json()
        assert "必须保留" not in second_view["issue"]["message"]
        assert "促销标语原文" in second_view["issue"]["message"]

        third_response = await _act(
            client,
            project["id"],
            "answer_requirements_clarification",
            text="1B",
            expected_revision=second_view["project_revision"],
        )
        assert third_response.status_code == 200, third_response.text
        third_view = third_response.json()
        assert "必须保留" not in third_view["issue"]["message"]
        assert "促销标语原文" in third_view["issue"]["message"]

        messages = await _messages(client, project["id"])
        assert "必须保留的商品特征：无（已确认）" in messages[-1]["content"]
        consultant_calls = [
            request.payload
            for request in stub.role_calls_received
            if request.payload["task"]["role"] == "requirements_consultant"
        ]
        assert any(
            item["question"].startswith("请说明必须保留")
            for item in consultant_calls[2]["input_data"]["clarification_answers"]
        )
        third_conversation = consultant_calls[2]["input_data"]["conversation"]
        assert any(
            item["role"] == "assistant" and first_questions[2] in item["text"]
            for item in third_conversation
        )
        assert any(
            item["role"] == "user" and item["text"] == "1A 2B 3C"
            for item in third_conversation
        )


async def test_corrupted_clarification_is_replaced_instead_of_displayed(full_settings):
    question = "请说明商品主体是什么\ufffd"
    first = _needs_input(
        None,
        question=question,
        path="product_description",
        detail="缺少必要信息",
    )
    script = Script([lambda request: first, _ready])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        assert "\ufffd" not in response.text

        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "MISSING_INPUT"
        assert "\ufffd" not in str(view)
        assert "商品主体" in view["issue"]["message"]

        messages = await _messages(client, project["id"])
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert "\ufffd" not in str(messages)


async def test_corrupted_ready_draft_is_rejected_before_any_candidate(full_settings):
    script = Script([lambda request: _ready(request, product_description="米色托特包\ufffd")])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert "\ufffd" not in response.text

        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert view["issue"]["code"] == "SERVICE_UNAVAILABLE"
        assert "\ufffd" not in str(view)
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 0
        assert jobs == 0
        assert visual.submit_calls == []


def _ready_data_for_questions(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "product_description": "米色托特包",
        "purpose": "电商商品主图",
        "facts": [],
        "creative_direction": None,
        "style_keywords": [],
        "composition": None,
        "lighting": None,
        "texts": [],
        "must_include": [],
        "must_preserve": [],
        "forbidden": [],
        "size": "1024x1024",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": [],
        "unresolved": ["商品主体尚未明确"],
    }
    data.update(overrides)
    return data


async def test_price_ambiguity_is_centralized_and_never_invented(full_settings):
    question = "请提供准确价格，或确认画面不显示价格。"
    data = _ready_data_for_questions(unresolved=["价格未明确"])
    script = Script(
        [
            lambda request: _needs_input(
                request,
                question=question,
                path="facts",
                detail="用户提到价格但未给出确定值",
                data=data,
            )
        ]
    )
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(
            client, project["id"], "做一个保温杯主图，价格你看着写，最好有促销感。"
        )
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "needs_input"
        assert view["issue"]["message"] == question
        assert view["artifacts"] == []
        assert "99" not in view["issue"]["message"]
        _, jobs = await _table_counts(application, project["id"])
        assert jobs == 0
        assert visual.submit_calls == []
        assert (
            stub.role_calls_received[2]
            .payload["input_data"]["user_text"]
            .startswith("做一个保温杯主图")
        )


async def test_open_ended_style_question_is_not_blocked_by_wording(full_settings):
    question = "请描述希望的整体风格与背景，例如：极简纯色背景、居家桌面场景、节日氛围布景。"
    data = _ready_data_for_questions(
        product_description="马克杯",
        purpose="主页展示",
        unresolved=["整体风格与背景未明确"],
    )
    script = Script(
        [
            lambda request: _needs_input(
                request,
                question=question,
                path="style_keywords",
                detail="用户希望制作马克杯主页展示图，但尚未说明整体风格与背景",
                data=data,
            )
        ]
    )
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "我想生成一张马克杯电商图，用于主页展示")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "MISSING_INPUT"
        assert view["issue"]["template_id"] is None
        assert view["issue"]["message"] == question
        assert view["artifacts"] == []
        assert stub.role_calls_received[-1].payload["task"]["role"] == "requirements_consultant"
        _, jobs = await _table_counts(application, project["id"])
        assert jobs == 0
        assert visual.submit_calls == []


async def test_unrequested_text_stays_empty_and_authorized_copy_is_preserved(full_settings):
    no_text_script = Script([_ready])
    async with _environment(full_settings, no_text_script) as (_, client, _, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张米色托特包商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        payload = [
            item["payload"]
            for item in response.json()["artifacts"]
            if item["kind"] == "brief_draft"
        ][0]
        assert payload["texts"] == []

    copy = "温暖一整天"
    copy_script = Script([lambda request: _ready(request, texts=[copy])])
    async with _environment(full_settings, copy_script) as (_, client, _, _):
        project = await _create_project(client)
        started = await _start(
            client,
            project["id"],
            "做一张米色托特包商品主图，文案写“温暖一整天”。",
        )
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        payload = [item["payload"] for item in view["artifacts"] if item["kind"] == "brief_draft"][
            0
        ]
        assert payload["texts"] == [copy]
        assert view["approvals"] == []
        assert view["generation_job"] is None


async def test_fabricated_text_without_user_authorization_is_rejected(full_settings):
    script = Script([lambda request: _ready(request, texts=["限时 8 折"])])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张米色托特包商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert script.brief_calls == 1
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 0
        assert jobs == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize("mode", ["missing", "foreign_evidence", "url"])
async def test_review_failure_is_a_typed_protocol_error_without_candidate_leak(
    full_settings, mode
):
    secret = "候选草稿不得回显"
    script = Script(
        [lambda request: _ready(request, product_description=secret, must_include=[secret])],
        review_modes=[mode],
    )
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        assert response.json()["error"]["message"] != templates.render("REVIEW_UNAVAILABLE")
        assert secret not in response.text

        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_INVALID"
        assert view["issue"]["template_id"] == "REVIEW_INVALID"
        assert view["issue"]["message"] == templates.render("REVIEW_INVALID")
        assert secret not in str(view)
        assert all(item["payload"] is None for item in view["artifacts"])
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 1
        assert jobs == 0
        assert visual.submit_calls == []


async def test_reviewer_outage_uses_fixed_template_without_candidate_leak(full_settings):
    secret = "候选草稿不得回显"
    script = Script(
        [lambda request: _ready(request, product_description=secret, must_include=[secret])],
        review_modes=["provider_error"],
    )
    async with _environment(full_settings, script) as (application, client, _, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        assert response.json()["error"]["message"] == templates.render("REVIEW_UNAVAILABLE")
        assert secret not in response.text

        view = await _view(client, project["id"])
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        assert secret not in str(view)
        assert all(item["payload"] is None for item in view["artifacts"])
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 1
        assert jobs == 0
        assert visual.submit_calls == []


async def test_needs_input_questions_skip_the_review_gate(full_settings):
    question = "请说明商品主体是什么？例如：A 米色托特包；B 黑色托特包。"
    script = Script(
        [
            lambda request: _needs_input(
                request,
                question=question,
                path="product_description",
                detail="缺少商品主体",
                data=_ready_data_for_questions(product_description=None),
            )
        ],
        review_modes=["missing"],
    )
    async with _environment(full_settings, script) as (_, client, _, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["status"] == "needs_input"
        assert view["issue"]["message"] == question
        assert view["artifacts"] == []
        assert script.review_calls == 0


async def test_review_accepts_extra_advisory_governance_checks(full_settings):
    script = Script([_ready], review_modes=["extra"])
    async with _environment(full_settings, script) as (_, client, _, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"
        assert script.review_calls == 1


async def test_review_accepts_advisory_not_applicable_without_evidence(full_settings):
    script = Script([_ready], review_modes=["advisory_na"])
    async with _environment(full_settings, script) as (_, client, _, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S04"
        assert script.review_calls == 1


async def test_required_review_check_still_requires_evidence(full_settings):
    script = Script([_ready], review_modes=["empty_required_evidence"])
    async with _environment(full_settings, script) as (_, client, _, _):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "REVIEW_INVALID"


async def test_revised_candidates_are_each_reviewed_and_budget_is_three(full_settings):
    script = Script([_ready], review_modes=["revise", "revise", "pass"])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"
        drafts = [item for item in view["artifacts"] if item["kind"] == "brief_draft"]
        assert [item["revision_no"] for item in drafts] == [1, 2, 3]
        assert [item["status"] for item in drafts] == [
            "superseded",
            "superseded",
            "approved",
        ]
        assert script.brief_calls == 3
        assert script.review_calls == 3
        produced = [
            request
            for request in stub.role_calls_received
            if request.payload["task"]["role"] == "requirements_consultant"
        ]
        assert [request.payload["task"]["attempt"] for request in produced] == [1, 2, 3]
        assert produced[1].payload["repair_codes"] == ["G03_STRUCTURE"]
        assert produced[2].payload["repair_codes"] == ["G03_STRUCTURE"]
        _, jobs = await _table_counts(application, project["id"])
        assert jobs == 0
        assert visual.submit_calls == []


async def test_exhausted_revision_budget_stops_without_a_fourth_candidate(full_settings):
    script = Script([_ready], review_modes=["revise"])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S03"
        assert view["status"] == "blocked"
        assert view["issue"]["template_id"] == "REVIEW_REJECTED"
        assert script.brief_calls == 3
        assert script.review_calls == 3
        drafts = [item for item in view["artifacts"] if item["kind"] == "brief_draft"]
        assert len(drafts) == 3
        assert drafts[-1]["payload"] is None
        _, jobs = await _table_counts(application, project["id"])
        assert jobs == 0
        assert visual.submit_calls == []


async def test_user_revision_after_reject_is_re_reviewed(full_settings):
    script = Script(
        [_ready, lambda request: _ready(request, product_description="米色帆布托特包")],
        review_modes=["reject", "pass"],
    )
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        first = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert first.status_code == 200, first.text
        blocked = first.json()
        assert blocked["status"] == "blocked"
        assert blocked["issue"]["template_id"] == "REVIEW_REJECTED"
        rejected = [item for item in blocked["artifacts"] if item["kind"] == "brief_draft"]
        assert len(rejected) == 1
        assert rejected[0]["status"] == "rejected"
        assert rejected[0]["payload"] is None

        revised = await _act(
            client,
            project["id"],
            "revise_brief_draft",
            text="商品主体改为米色帆布托特包。",
            expected_revision=blocked["project_revision"],
        )
        assert revised.status_code == 200, revised.text
        view = revised.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"
        drafts = [item for item in view["artifacts"] if item["kind"] == "brief_draft"]
        assert [item["revision_no"] for item in drafts] == [1, 2]
        assert [item["status"] for item in drafts] == ["rejected", "approved"]
        assert drafts[-1]["payload"]["product_description"] == "米色帆布托特包"
        assert script.brief_calls == 2
        assert script.review_calls == 2
        _, jobs = await _table_counts(application, project["id"])
        assert jobs == 0
        assert visual.submit_calls == []


async def test_technical_failure_is_not_retried(full_settings):
    script = Script(
        [
            ProviderError(
                "provider down",
                evidence="uncertain",
                error_code="LLM_UNAVAILABLE",
            )
        ]
    )
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert script.brief_calls == 1
        view = await _view(client, project["id"])
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "SERVICE_UNAVAILABLE"
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 0
        assert jobs == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "facts": [
                {
                    "text": "伪造来源",
                    "basis": "user_declared",
                    "evidence_ids": ["foreign-evidence"],
                }
            ]
        },
        {"size": "2048x2048"},
        {
            "facts": [
                {
                    "text": "图片中看到白色杯子",
                    "basis": "image_observed",
                    "evidence_ids": ["user:1"],
                }
            ]
        },
        {"product_description": "   "},
        {"product_description": None},
        {"purpose": ""},
        {"unresolved": ["价格未明确"]},
    ],
)
async def test_business_source_and_size_validation_rejects_bad_drafts(full_settings, overrides):
    script = Script([lambda request: _ready(request, **overrides)])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品主图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        if "size" in overrides:
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "needs_input"
            assert response.json()["issue"]["code"] == "SIZE_UNSUPPORTED"
        else:
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert script.brief_calls == 1
        assert script.review_calls == 0
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 0
        assert jobs == 0
        assert visual.submit_calls == []


async def test_invalid_provider_json_offers_continue_and_recovers(full_settings):
    question = "请说明商品主体是什么？例如：A 米色托特包；B 黑色托特包。"
    script = Script(
        [
            ProviderError(
                "bad json", evidence="uncertain", error_code="LLM_RESPONSE_INVALID"
            ),
            _needs_input(
                None,
                question=question,
                path="product_description",
                detail="缺少商品主体",
            ),
        ]
    )
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "LLM_RESPONSE_INVALID"

        failed = await _view(client, project["id"])
        assert failed["stage"] == "S03"
        assert failed["status"] == "failed"
        assert failed["issue"]["code"] == "LLM_RESPONSE_INVALID"
        assert [item["id"] for item in failed["available_actions"]] == [
            "continue_workflow"
        ]

        continued = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=failed["project_revision"],
        )
        assert continued.status_code == 200, continued.text
        view = continued.json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "MISSING_INPUT"
        assert question in view["issue"]["message"]
        assert script.brief_calls == 2


async def test_no_product_ready_brief_is_normalized_and_continue_recovers(full_settings):
    def invalid(request):
        return _ready(request, product_description=None)

    script = Script([invalid, _ready_no_product])
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(
            client, project["id"], "做一个海边人物写真，画面不含商品，也不要文字"
        )
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        failed = await _view(client, project["id"])
        assert failed["stage"] == "S03"
        assert failed["status"] == "failed"
        assert [item["id"] for item in failed["available_actions"]] == ["continue_workflow"]

        continued = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=failed["project_revision"],
        )
        assert continued.status_code == 200, continued.text
        view = continued.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"
        drafts = [item for item in view["artifacts"] if item["kind"] == "brief_draft"]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "approved"
        assert drafts[0]["payload"]["product_description"] == "画面不含商品，只呈现人物写真。"
        assert drafts[0]["payload"]["purpose"] == "活动海报"
        assert drafts[0]["payload"]["texts"] == []
        messages = await _messages(client, project["id"])
        report = messages[-1]["content"]
        assert report.startswith("需求对齐情况")
        assert "画面主体：画面不含商品，只呈现人物写真。" in report
        assert "商品主体：" not in report
        assert "必须保留的画面特征：" in report
        assert "必须保留的商品特征" not in report
        assert "还缺少的信息\n- 无，需求信息已对齐。" in report

        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 1
        assert jobs == 0
        assert script.brief_calls == 2
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_off_mode_skips_s01_and_s03_reviewers(full_settings):
    settings = full_settings.model_copy(update={"workflow_gate_mode": "off"})
    script = Script([_ready])
    async with _environment(settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "做一张米色托特包商品主图")

        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S04"
        assert view["status"] == "active"

        roles = [request.payload["task"]["role"] for request in stub.role_calls_received]
        assert roles == ["intent_router", "requirements_consultant"]
        assert script.review_calls == 0
        assert visual.submit_calls == []
