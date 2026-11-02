from __future__ import annotations

import asyncio
import json
import types
import uuid
from typing import Any

import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.auth.models import Account
from app.db import write_transaction
from app.workflow.projection import build_task_state
from app.workflow.reasons import ReasonCode, classify_issue_code
from app.workflow.schemas_workflow import WorkflowVersionView
from app.workflow.tables import Approval, Artifact, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub
from tests.support.auth import TEST_ORIGIN, AnonymousClient
from tests.test_t59_isolation import _add_account, _login_user
from tests.test_t65_generation_cycle import _environment
from tests.test_v2_t40_prompt_review import png_bytes
from tests.test_v3_p1_t10_execution_snapshot import (
    _confirmation,
    _execution_responder,
    _setup_execution_snapshot,
)

_SENTINEL = "RAW_REVIEWER_TEXT_SENTINEL_42"


def _new_stubs() -> tuple[LLMStub, VisualStub]:
    return (
        LLMStub(),
        VisualStub(
            submit_outcome=ProviderOutcome(
                state="succeeded", image_bytes=png_bytes(64, 64), image_count=1
            )
        ),
    )


async def _workflow(client: Any, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def test_task_state_projects_progress_binding_constraints_and_effects(
    full_settings,
) -> None:
    llm, visual = _new_stubs()
    llm.gate = asyncio.Event()
    async with _environment(full_settings, llm, visual, _execution_responder) as (
        app,
        client,
    ):
        setup = await _setup_execution_snapshot(app, client)
        confirmed = await client.post(
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
        assert confirmed.status_code == 200, confirmed.text
        view = await _workflow(client, setup["project_id"])
        task = view["task_state"]
        assert task["progress"]["phase"] == "prompt"
        assert task["progress"]["stage"] == "S07"
        assert task["progress"]["stage_label"]
        assert task["progress"]["terminal"] is False
        assert task["binding"]["project_id"] == setup["project_id"]
        assert task["binding"]["requirement_revision"] == setup["revision"]
        assert task["binding"]["execution_snapshot_digest"] == _confirmation(view)[
            "snapshot_digest"
        ]
        assert task["binding"]["current"] is True
        constraints = {item["path"]: item for item in task["constraints"]}
        assert constraints["must_include"]["strength"] == "hard"
        assert constraints["background"]["strength"] == "soft"
        assert constraints["background"]["label"] == "背景"
        assert constraints["must_include"]["label"].startswith("必须包含")
        assert task["resource_state"]["current_state"] == "none"
        assert task["resource_state"]["current_version"] is None
        assert "%" not in json.dumps(task, ensure_ascii=False)
        action = next(
            item for item in view["available_actions"] if item["id"] == "compile_prompt"
        )
        assert action["effect"] == "model_call"
        assert action["read_only"] is False
        assert action["requires_model_call"] is True
        assert action["automatic_retry"] is False
        assert all(item["id"] != "cancel_generation" for item in view["available_actions"])


async def test_safe_approval_projection_strips_unreviewed_payloads(full_settings) -> None:
    llm, visual = _new_stubs()
    llm.gate = asyncio.Event()
    async with _environment(full_settings, llm, visual, _execution_responder) as (
        app,
        client,
    ):
        setup = await _setup_execution_snapshot(app, client)
        await client.post(
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
        async with write_transaction(app.state.session_factory) as session:
            approval = await session.scalar(
                select(Approval).where(Approval.approval_type == "requirements_card")
            )
            assert approval is not None
            checks = json.loads(approval.checks_json)
            checks["reviewer_note"] = _SENTINEL
            checks["candidate_body"] = {"raw": _SENTINEL}
            checks["private_locator"] = f"https://private.invalid/{_SENTINEL}"
            approval.checks_json = json.dumps(checks, ensure_ascii=False)
            await session.flush()
        response = await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )
        assert response.status_code == 200
        assert _SENTINEL not in response.text
        view = response.json()
        approval_view = next(
            item
            for item in view["approvals"]
            if item["approval_type"] == "requirements_card"
        )
        assert set(approval_view["checks"]) == {"confirmation"}
        assert approval_view["checks"]["confirmation"]["snapshot_digest"]


async def test_unapproved_artifact_payload_is_not_projected(full_settings) -> None:
    llm, visual = _new_stubs()
    async with _environment(full_settings, llm, visual, _execution_responder) as (
        app,
        client,
    ):
        setup = await _setup_execution_snapshot(app, client)
        sentinel = "RAW_CANDIDATE_BODY_SENTINEL_13"
        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == setup["project_id"]
                )
            )
            assert run is not None
            session.add(
                Artifact(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    project_id=setup["project_id"],
                    kind="prompt_plan",
                    revision_no=99,
                    status="candidate",
                    payload_json=json.dumps({"prompt": sentinel}),
                    digest="a" * 64,
                    created_at="2026-01-01T00:00:00.000Z",
                )
            )
        view = await _workflow(client, setup["project_id"])
        candidate = next(
            item for item in view["artifacts"] if item["kind"] == "prompt_plan"
        )
        assert candidate["status"] == "candidate"
        assert candidate["payload"] is None
        assert sentinel not in json.dumps(view, ensure_ascii=False)


async def test_safe_reason_is_typed_and_does_not_read_raw_issue_text(
    full_settings,
) -> None:
    llm, visual = _new_stubs()
    async with _environment(full_settings, llm, visual, _execution_responder) as (
        app,
        client,
    ):
        setup = await _setup_execution_snapshot(app, client)
        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == setup["project_id"]
                )
            )
            assert run is not None
            run.stage = "S12"
            run.status = "failed"
            run.issue_code = "GENERATION_UNKNOWN"
            run.template_id = "GENERATION_UNKNOWN"
            run.issue_message = _SENTINEL
            run.template_params_json = "{}"
            await session.flush()
        view = await _workflow(client, setup["project_id"])
        reason = view["task_state"]["reason"]
        assert reason["category"] == "remote_unknown"
        assert reason["severity"] == "unknown"
        assert reason["remote_unknown"] is True
        assert reason["auto_retry"] is False
        assert reason["requires_user_input"] is False
        assert _SENTINEL not in json.dumps(view, ensure_ascii=False)
        assert view["task_state"]["progress"]["phase"] == "recovery"
        action = next(
            item
            for item in view["available_actions"]
            if item["id"] == "continue_workflow"
        )
        assert action["effect"] == "model_call"
        assert action["retry_mode"] == "manual_only"
        assert action["automatic_retry"] is False

        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == setup["project_id"]
                )
            )
            assert run is not None
            run.template_id = None
            run.issue_message = _SENTINEL
            await session.flush()
        fallback_view = await _workflow(client, setup["project_id"])
        assert fallback_view["issue"]["message"] == ""
        assert _SENTINEL not in json.dumps(fallback_view, ensure_ascii=False)


def test_resource_state_does_not_promote_an_old_success() -> None:
    version = WorkflowVersionView(
        run_id="run-1",
        sequence_no=1,
        requirement_revision=1,
        status="published",
        generation_job_id="job-old",
        asset_id="asset-old",
        created_at="2026-11-02T00:00:00.000Z",
    )
    project = types.SimpleNamespace(id="project-1", revision=2)
    run = types.SimpleNamespace(
        id="run-1",
        stage="S10",
        status="completed",
        issue_code=None,
        template_id=None,
        template_params_json=None,
        requirement_revision=1,
    )
    job = types.SimpleNamespace(
        id="job-old",
        status="published",
        confirmed_snapshot_digest="old-digest",
    )
    delivery = types.SimpleNamespace(
        id="delivery-1", status="published", asset_id="asset-old"
    )
    task = build_task_state(
        project=project,
        run=run,
        job=job,
        delivery=delivery,
        versions=[version],
        intent=None,
        active_snapshot={"snapshot_digest": "new-digest", "constraints": []},
        artifacts=[],
    )
    assert task.binding.current is False
    assert task.resource_state.current_state == "history_only"
    assert task.resource_state.current_version is None
    assert [item.asset_id for item in task.resource_state.history] == ["asset-old"]

    project.revision = 1
    job.confirmed_snapshot_digest = "new-digest"
    current = build_task_state(
        project=project,
        run=run,
        job=job,
        delivery=delivery,
        versions=[version],
        intent=None,
        active_snapshot={"snapshot_digest": "new-digest", "constraints": []},
        artifacts=[],
    )
    assert current.binding.current is True
    assert current.resource_state.current_state == "published"
    assert current.resource_state.current_version is not None
    assert current.resource_state.current_version.asset_id == "asset-old"
    assert current.resource_state.history == []


async def test_projection_rejects_cross_account_anonymous_and_disabled_reads(
    full_settings,
) -> None:
    llm, visual = _new_stubs()
    async with _environment(full_settings, llm, visual, _execution_responder) as (
        app,
        client,
    ):
        setup = await _setup_execution_snapshot(app, client)
        other = await _add_account(app, "t13-other@example.com")
        other_client = await _login_user(app, "t13-other@example.com")
        own = await other_client.post("/api/projects", json={})
        assert own.status_code == 201, own.text
        other_project_id = own.json()["id"]

        assert (
            await other_client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).status_code == 404
        assert (
            await client.get(f"/api/projects/{other_project_id}/workflow")
        ).status_code == 404

        anonymous = AnonymousClient(
            transport=ASGITransport(app=app), base_url=TEST_ORIGIN
        )
        async with anonymous:
            response = await anonymous.get(
                f"/api/projects/{setup['project_id']}/workflow"
            )
            assert response.status_code == 401

        async with write_transaction(app.state.session_factory) as session:
            account = await session.get(Account, other.id)
            assert account is not None
            account.is_disabled = 1
            account.disabled_at = "2026-11-02T00:00:00.000Z"
            await session.flush()
        response = await other_client.get(
            f"/api/projects/{other_project_id}/workflow"
        )
        assert response.status_code in {401, 403}


async def test_normal_and_recovery_share_the_same_safe_reason(full_settings) -> None:
    llm, visual = _new_stubs()
    async with _environment(full_settings, llm, visual, _execution_responder) as (
        app,
        client,
    ):
        setup = await _setup_execution_snapshot(app, client)
        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == setup["project_id"]
                )
            )
            assert run is not None
            run.stage = "S08"
            run.status = "failed"
            run.issue_code = "SERVICE_UNAVAILABLE"
            run.template_id = "SERVICE_UNAVAILABLE"
            run.template_params_json = "{}"
            await session.flush()
        normal = (await _workflow(client, setup["project_id"]))["task_state"]["reason"]
        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == setup["project_id"]
                )
            )
            assert run is not None
            run.stage = "S12"
            await session.flush()
        recovered = (await _workflow(client, setup["project_id"]))["task_state"][
            "reason"
        ]
        assert normal == recovered
        assert normal["category"] == "technical_failure"
        assert normal["auto_retry"] is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("MISSING_INPUT", ReasonCode.MISSING_INFORMATION),
        ("REVIEW_REVISE", ReasonCode.REPAIRABLE_COMPOSITION),
        ("REQUIREMENT_DRIFT", ReasonCode.TRUE_DRIFT),
        ("HARD_REQUIREMENT_FAILED", ReasonCode.HARD_FAILURE),
        ("REVIEW_REJECTED", ReasonCode.CONTENT_REJECTION),
    ("UNSAFE_REQUEST", ReasonCode.CONTENT_REJECTION),
        ("CANDIDATE_EXPIRED", ReasonCode.MISSING_EVIDENCE),
        ("PROMPT_REVIEW_INVALID", ReasonCode.PROTOCOL_ERROR),
        ("SERVICE_UNAVAILABLE", ReasonCode.TECHNICAL_FAILURE),
        ("GENERATION_UNKNOWN", ReasonCode.REMOTE_UNKNOWN),
        ("ACCOUNT_DISABLED", ReasonCode.AUTHORIZATION_INVALID),
    ],
)
def test_issue_codes_cover_every_safe_category(code: str, expected: ReasonCode) -> None:
    assert classify_issue_code(code) == expected
