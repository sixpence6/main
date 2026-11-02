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
from app.workflow.stages.s03 import REVIEW_RULE_IDS
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
        assert [message["content"] for message in messages] == [text]
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
async def test_missing_subject_or_purpose_is_a_reviewed_central_question(
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
        assert roles[-2:] == ["requirements_consultant", "output_reviewer"]
        assert stub.role_calls_received[-1].payload["review_targets"][0]["questions"] == [question]

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
        assert [message["role"] for message in messages] == ["user", "user"]
        assert messages[-1]["content"] == "是一只米色托特包，用于电商商品主图。"
        _, jobs = await _table_counts(application, project["id"])
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


@pytest.mark.parametrize("mode", ["missing", "foreign_evidence", "url", "provider_error"])
async def test_review_failure_uses_fixed_template_without_candidate_leak(full_settings, mode):
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
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        assert response.json()["error"]["message"] == templates.render("REVIEW_UNAVAILABLE")
        assert secret not in response.text

        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert view["issue"]["code"] == "REVIEW_UNAVAILABLE"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        assert view["issue"]["message"] == templates.render("REVIEW_UNAVAILABLE")
        assert secret not in str(view)
        assert all(item["payload"] is None for item in view["artifacts"])
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 1
        assert jobs == 0
        assert visual.submit_calls == []


async def test_needs_input_questions_are_not_shown_when_review_fails(full_settings):
    secret = "这是审查前不应展示的内部问题"
    script = Script(
        [
            lambda request: _needs_input(
                request,
                question=secret,
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
        assert response.status_code == 503, response.text
        view = await _view(client, project["id"])
        assert secret not in str(view)
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        assert view["artifacts"] == []


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
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert view["issue"]["code"] == "REVIEW_UNAVAILABLE"


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
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
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
        assert blocked["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
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
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert script.brief_calls == 1
        assert script.review_calls == 0
        artifacts, jobs = await _table_counts(application, project["id"])
        assert artifacts == 0
        assert jobs == 0
        assert visual.submit_calls == []
