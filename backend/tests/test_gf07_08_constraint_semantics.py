from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.adapters.base import ProviderOutcome
from tests.support.adapters import LLMStub, VisualStub
from tests.support.t02_prompt import final_click, ready_project
from tests.support.t02_prompt import responder as prompt_responder
from tests.test_t65_generation_cycle import _drain
from tests.test_t65_generation_cycle import _environment as _generation_environment
from tests.test_v2_t05_requirements import (
    Script,
    _act,
    _create_project,
    _environment,
    _needs_input,
    _ready_data_for_questions,
    _start,
)
from tests.test_v2_t40_prompt_review import png_bytes


def _invalid_question(question: str) -> dict[str, Any]:
    data = _ready_data_for_questions(product_description=None)
    return _needs_input(
        None,
        question=question,
        path="product_description",
        detail="缺少必要信息",
        data=data,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "建议使用红色背景，并把商品放右侧。",
        "请说明商品主体，详见 https://evil.invalid/brief",
        "请" + "说" * 320,
    ],
)
async def test_unsafe_clarification_question_is_sanitized_not_blocked(
    full_settings: Any, question: str
) -> None:
    script = Script([lambda request: _invalid_question(question)])
    async with _environment(full_settings, script) as (_, client, _stub, visual):
        project = await _create_project(client)
        started = await _start(client, project["id"], "帮我做一张商品图")
        response = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert response.status_code == 200, response.text
        view = (
            await client.get(f"/api/projects/{project['id']}/workflow")
        ).json()
        assert view["stage"] == "S03"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "MISSING_INPUT"
        assert question not in str(view)
        assert view["issue"]["message"]
        assert view["artifacts"] == []
        assert view["generation_job"] is None
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_soft_preference_feedback_does_not_veto_a_passing_image(
    full_settings: Any,
) -> None:
    def responder(request: Any) -> dict[str, Any]:
        response = prompt_responder(request)
        if request.payload["task"].get("role") == "quality_compliance":
            response["data"]["soft_feedback"] = [
                "背景氛围偏暖，仅作为软偏好记录，不否定硬性检查结果"
            ]
        return response

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _generation_environment(
        full_settings, llm, visual, responder
    ) as (app, client):
        setup = await ready_project(app, client)
        clicked = await final_click(client, setup, command_id=str(uuid.uuid4()))
        assert clicked.status_code == 200, clicked.text
        await _drain(app)
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S10"
        assert view["generation_job"]["status"] in {"quarantined", "approved"}
        assert len(visual.submit_calls) == 1
