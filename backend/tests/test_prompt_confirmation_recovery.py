"""HTTP regressions for empty prompt rejection and legacy snapshot recovery."""

import json

import pytest
from sqlalchemy import func, select

from app.adapters.base import ProviderOutcome
from app.final_confirmation import FinalAuthorization
from app.prompt_preparation import PreparedPrompt
from app.workflow.contracts import digest_payload
from app.workflow.tables import Approval, Artifact, GenerationJob
from tests.test_collaborative_revisions import settings_for
from tests.test_editable_prompt_confirmation import action, final_payload
from tests.test_markdown_handoff import (
    BOARD_MARKDOWN,
    BOARD_NEGATIVE,
    BOARD_POSITIVE,
    BoardMarkdownScript,
    start_board,
)
from tests.test_t65_generation_cycle import _drain
from tests.test_v2_t05_requirements import _create_project, _environment, _view
from tests.test_v2_t40_prompt_review import png_bytes


@pytest.mark.parametrize("document", [
    pytest.param("# 正向提示词\n\n## 反向提示词", id="headings-only"),
    pytest.param("```markdown\n\n```", id="empty-fence"),
    pytest.param("", id="empty-model-output"),
])
async def test_empty_model_prompt_blocks_and_exposes_explicit_recovery(full_settings, document):
    async with _environment(settings_for(full_settings), BoardMarkdownScript(document)) as (app, client, llm, visual):
        project = await _create_project(client)
        response = await start_board(app, client, project["id"])
        await _drain(app)
        view = await _view(client, project["id"])
        assert visual.submit_calls == []
        preview = view.get("prompt_preview")
        assert preview is None or preview["can_confirm"] is False, preview
        assert "confirm_prompt" not in {item["id"] for item in view["available_actions"]}
        assert "PROMPT_EMPTY" in json.dumps([response, view]), view
        recovery_actions = {item["id"] for item in view["available_actions"]}
        assert recovery_actions & {"compile_prompt", "revise_prompt", "continue_workflow", "edit_prepared_prompt"}, view
        calls = len(llm.role_calls_received)
        await _view(client, project["id"])
        await _drain(app)
        assert len(llm.role_calls_received) == calls and visual.submit_calls == []


async def seed_bound_legacy_empty_snapshot(app, snapshot_id):
    """Keep all bindings valid so only the missing-body guard rejects this data."""
    async with app.state.session_factory() as session:
        snapshot = await session.get(PreparedPrompt, snapshot_id)
        artifact = await session.get(Artifact, snapshot.artifact_id)
        approval = await session.get(Approval, snapshot.approval_id)
        plan = json.loads(artifact.payload_json)
        plan.update(positive_prompt="## 正向提示词", negative_prompt="")
        artifact.payload_json = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        artifact.digest = digest_payload(plan)
        approval.artifact_digest = artifact.digest
        frozen = json.loads(snapshot.request_json)
        frozen.update(positive_prompt="## 正向提示词", negative_prompt="", prompt="## 正向提示词",
                      prompt_artifact_digest=artifact.digest)
        snapshot.request_json = json.dumps(frozen, ensure_ascii=False, sort_keys=True)
        snapshot.request_digest = digest_payload(frozen)
        await session.commit()


async def legacy_waiting(app, client):
    project = await _create_project(client)
    await start_board(app, client, project["id"])
    await _drain(app)
    initial = await _view(client, project["id"])
    await seed_bound_legacy_empty_snapshot(app, initial["prompt_preview"]["id"])
    return project, await _view(client, project["id"])


async def test_bound_legacy_empty_snapshot_direct_confirmation_is_rejected(full_settings):
    async with _environment(settings_for(full_settings), BoardMarkdownScript()) as (app, client, llm, visual):
        project, view = await legacy_waiting(app, client)
        preview = view["prompt_preview"]
        # Bypass the UI even when its server-provided button is disabled.
        denied = await action(client, project["id"], view, "confirm_prompt", final_payload(preview))
        assert denied.status_code == 409, denied.text
        assert denied.json()["error"]["code"] == "PROMPT_EMPTY", denied.text
        await _drain(app)
        assert visual.submit_calls == []
        async with app.state.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(FinalAuthorization)) == 0
            assert await session.scalar(select(func.count()).select_from(GenerationJob)) == 0


async def test_bound_legacy_empty_snapshot_can_be_read_edited_and_confirmed_without_model_calls(full_settings):
    async with _environment(settings_for(full_settings), BoardMarkdownScript()) as (app, client, llm, visual):
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1)
        project, view = await legacy_waiting(app, client)
        preview = view["prompt_preview"]
        assert preview["source_markdown"] == BOARD_MARKDOWN
        assert preview["parse_warning"]
        assert preview["can_confirm"] is False and preview["can_edit"] is True
        assert [item["id"] for item in view["available_actions"]] == ["edit_prepared_prompt"]
        calls = len(llm.role_calls_received)
        assert (await _view(client, project["id"]))["prompt_preview"] == preview
        edited = await action(client, project["id"], view, "edit_prepared_prompt", {
            "snapshot_id": preview["id"], "snapshot_digest": preview["request_digest"],
            "positive_prompt": BOARD_POSITIVE, "negative_prompt": BOARD_NEGATIVE})
        assert edited.status_code == 200, edited.text
        current = await _view(client, project["id"])
        newer = current["prompt_preview"]
        assert newer["id"] != preview["id"] and newer["request_digest"] != preview["request_digest"]
        assert newer["can_confirm"] is True
        assert newer["request"]["positive_prompt"] == BOARD_POSITIVE
        assert newer["request"]["negative_prompt"] == BOARD_NEGATIVE
        assert any(item["id"] == "confirm_prompt" for item in current["available_actions"])
        assert len(llm.role_calls_received) == calls and visual.submit_calls == []
        stale = await action(client, project["id"], current, "confirm_prompt", final_payload(preview))
        assert stale.status_code == 409, stale.text
        async with app.state.session_factory() as session:
            assert (await session.get(PreparedPrompt, preview["id"])).status == "invalidated"
        confirmed = await action(client, project["id"], current, "confirm_prompt", final_payload(newer))
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        assert len(visual.submit_calls) == 1 and len(llm.role_calls_received) == calls
        assert visual.submit_calls[0].prompt == f"{BOARD_POSITIVE}\n\n禁止内容（Negative prompt）:\n{BOARD_NEGATIVE}"
