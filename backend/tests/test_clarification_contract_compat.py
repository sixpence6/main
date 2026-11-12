"""Regression for the observed valid-JSON needs_input / issues=[] failure."""
import pytest

from app.workflow.contracts import ContractError, validate_role_output
from tests.test_collaborative_revisions import answer, settings_for
from tests.test_role_chain_consolidation import ConsolidatedScript
from tests.test_v2_t05_requirements import _create_project, _environment, _messages, _start, _view

QUESTION = {
    "id": "cover_direction", "field": "creative_direction", "purpose": "direction",
    "delegatable": True, "text": "封面想采用哪种画面方向？",
    "options": [
        {"id": "A", "label": "果肉特写", "value": "明亮果肉特写"},
        {"id": "B", "label": "整果陈列", "value": "干净背景整果陈列"},
        {"id": "C", "label": "你帮我选", "value": "推荐画面方向", "delegates": True},
    ],
}


def observed_shape(request, *, default_size=False):
    output = answer(request, status="needs_input", questions=[QUESTION],
                    product_description="橙子", purpose="商品封面")
    output["issues"] = []
    if default_size:
        output["data"]["updates"] = [{"field": "size", "operation": "set",
                                      "value": output["data"]["brief"]["size"], "evidence_ids": []}]
    return output


@pytest.mark.parametrize("default_size", [False, True])
async def test_normal_clarification_does_not_fail_as_unparseable(full_settings, default_size):
    def respond(request):
        return observed_shape(request, default_size=default_size)

    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[respond])) as (app, client, llm, visual):
        project = await _create_project(client)
        await _start(client, project["id"], "帮我做一张橙子的商品封面")
        view = await _view(client, project["id"])
        assert view["stage"] == "S03" and view["status"] == "needs_input", view
        assert view["conversation_question"]["field"] == "creative_direction"
        assert len(visual.submit_calls) == 0 and len(llm.role_calls_received) == 1
        assert (await _view(client, project["id"]))["conversation_question"] == view["conversation_question"]
        messages = await _messages(client, project["id"])
        assert not any("模型响应无法解析" in m["content"] for m in messages)


def test_needs_input_still_requires_real_structured_questions():
    raw = {"role": "requirements_assistant", "status": "needs_input", "data": {
        "scope": "image_request", "kind": "clarification", "reference_usage": "none",
        "brief": None, "updates": [], "clarifications": []}, "issues": [], "questions": []}
    with pytest.raises(ContractError):
        validate_role_output("requirements_assistant", raw)


@pytest.mark.parametrize("default_size", [False, True])
async def test_manual_recovery_reuses_saved_question_without_new_model_call(full_settings, monkeypatch, default_size):
    from app.workflow import roles
    from tests.test_v2_t05_requirements import _act

    original = roles.validate_role_output

    def old_validation(role, raw):
        if role == "requirements_assistant":
            raise ContractError("needs_input 必须至少一个 issue")
        return original(role, raw)

    def respond(request):
        return observed_shape(request, default_size=default_size)

    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[respond])) as (app, client, llm, visual):
        project = await _create_project(client)
        monkeypatch.setattr(roles, "validate_role_output", old_validation)
        failed = await _act(client, project["id"], "submit_request",
                            text="帮我做橙子商品封面", expected_revision=project["revision"])
        assert failed.status_code == 503 and "LLM_RESPONSE_INVALID" in failed.text
        view = await _view(client, project["id"])
        assert view["status"] == "failed"
        monkeypatch.setattr(roles, "validate_role_output", original)
        recovered = await _act(client, project["id"], "continue_workflow",
                               expected_revision=view["project_revision"])
        assert recovered.status_code == 200, recovered.text
        view = recovered.json()
        assert view["status"] == "needs_input" and view["stage"] == "S03"
        assert view["conversation_question"]["field"] == "creative_direction"
        assert len(llm.role_calls_received) == 1 and len(visual.submit_calls) == 0


async def test_no_evidence_cannot_change_a_user_field(full_settings):
    def respond(request):
        raw = observed_shape(request)
        raw["data"]["updates"] = [{"field": "product_description", "operation": "set",
                                    "value": "未经用户说明的其他商品", "evidence_ids": []}]
        return raw

    from tests.test_v2_t05_requirements import _act
    async with _environment(settings_for(full_settings), ConsolidatedScript(answers=[respond])) as (app, client, llm, visual):
        project = await _create_project(client)
        response = await _act(client, project["id"], "submit_request", text="做橙子封面",
                              expected_revision=project["revision"])
        assert response.status_code == 409 and "REQUIREMENT_SOURCE_INVALID" in response.text
        assert len(visual.submit_calls) == 0
