from __future__ import annotations

from typing import Any

import pytest

from app.adapters.base import ProviderError
from app.timeutil import format_timestamp, utc_now
from app.workflow import adjudication, prompts
from app.workflow.stages import (
    s01,
    s02,
    s03,
    s04,
    s05,
    s06,
    s07,
    s08,
    s09,
    s10,
    s11,
    s12,
)
from app.workflow.tables import Approval
from tests.test_v2_t13_delivery import (
    _delivery_env,
    _post_action,
    _role_responder,
    _seed_ready_state,
)

STAGE_ACTIONS = {
    "S01": ("submit_request", "answer_intent_clarification"),
    "S02": ("analyze_reference", "confirm_reference_usage"),
    "S03": (
        "compose_brief",
        "answer_requirements_clarification",
        "revise_brief_draft",
    ),
    "S04": ("plan_creative", "revise_creative"),
    "S05": ("plan_visual", "revise_visual", "finalize_requirements"),
    "S06": ("confirm_requirements", "revise_requirements"),
    "S07": ("compile_prompt", "revise_prompt", "confirm_prompt", "abandon_generation"),
    "S08": (
        "submit_generation",
        "verify_generation",
        "retry_generation",
        "recover_generation",
        "close_generation_tracking",
        "start_new_generation",
    ),
    "S09": (
        "review_candidate",
        "recheck_candidate",
        "request_correction",
        "retry_rejected_generation",
        "revise_prompt_and_retry",
    ),
    "S10": (
        "prepare_delivery",
        "approve_delivery",
        "publish_delivery",
        "request_new_version",
    ),
    "S11": ("retrieve_original_result", "review_legacy_item"),
    "S12": (
        "continue_workflow",
        "recover_submission",
        "resume_after_restart",
        "cleanup_expired",
    ),
}

STAGE_MODULES = {
    "S01": s01,
    "S02": s02,
    "S03": s03,
    "S04": s04,
    "S05": s05,
    "S06": s06,
    "S07": s07,
    "S08": s08,
    "S09": s09,
    "S10": s10,
    "S11": s11,
    "S12": s12,
}

REVIEW_CATALOGUES = {
    "S01": (s01._RULESET, {"G01_SCOPE", "G01_ACTION"}),
    "S02": (
        s02._RULESET,
        {
            "G02_IMAGE_VISIBLE",
            "G02_OBSERVATION_GROUNDED",
            "G02_TEXT_IS_DATA",
            "G02_NO_UNSUPPORTED_INFERENCE",
        },
    ),
    "S03": (
        s03._RULESET,
        {
            "G03_STRUCTURE",
            "G03_SUBJECT_PURPOSE",
            "G03_SOURCE_BASIS",
            "G03_TEXTS",
            "G03_CONSTRAINTS",
            "G03_SIZE",
            "G03_UNRESOLVED",
        },
    ),
    "S04": (
        s04.RULESET,
        {"G03_SOURCE", "G03_COPY", "G03_REQUIREMENTS", "G03_SINGLE_DIRECTION"},
    ),
    "S05_VISUAL": (
        s05.RULESET_VISUAL,
        {
            "S05_STYLE",
            "S05_COMPOSITION",
            "S05_BACKGROUND",
            "S05_PALETTE",
            "S05_LIGHTING",
            "S05_TEXT_LAYOUT",
            "S05_REFERENCE_TREATMENT",
            "S05_PRESERVATION",
        },
    ),
    "S05_CARD": (
        s05.RULESET_CARD,
        {
            "S06_PRODUCT_SOURCE",
            "S06_PURPOSE",
            "S06_CREATIVE",
            "S06_STYLE",
            "S06_COMPOSITION_LIGHTING",
            "S06_REFERENCE",
            "S06_TEXT",
            "S06_CONSTRAINTS",
            "S06_SIZE",
            "S06_BLOCKERS",
        },
    ),
    "S07": (
        adjudication.s07_rules(),
        {"G03_CONFIRMATION", "G05_EXECUTION_BOUNDARY"},
    ),
    "S09_REPORT": (s09._quality_ruleset(), {"review:quality_report"}),
    "S09_IMAGE": (
        s09._image_ruleset(s09._rule_specs(False)),
        {
            "hard:subject",
            "hard:product_consistency",
            "hard:quantity",
            "hard:text_logo",
            "hard:forbidden",
            "hard:composition",
            "hard:size_format",
        },
    ),
    "S10": (s10._MANIFEST_RULESET, set(s10._MANIFEST_RULES)),
    "S11_TEXT": (
        s11._review_ruleset("legacy_message", s11.LEGACY_TEXT_RULES),
        set(s11.LEGACY_TEXT_RULES),
    ),
    "S11_PROMPT": (
        s11._review_ruleset("legacy_generation_prompt", s11.LEGACY_PROMPT_RULES),
        set(s11.LEGACY_PROMPT_RULES),
    ),
    "S11_REPORT": (
        s11._review_ruleset("quality_report", s11.LEGACY_REPORT_RULES),
        set(s11.LEGACY_REPORT_RULES),
    ),
    "S11_DELIVERY": (
        s11._review_ruleset("delivery_manifest", s11.LEGACY_DELIVERY_RULES),
        set(s11.LEGACY_DELIVERY_RULES),
    ),
}


def test_four_personas_cover_every_work_unit_role() -> None:
    labels = {
        "appearance": "外观",
        "planner": "策划",
        "maker": "制图",
        "inspector": "质检",
    }
    assert set(prompts.PERSONA_INCREMENTS) == set(labels)
    assert set(prompts.PERSONA_BY_ROLE) == set(prompts.ROLE_INCREMENTS)
    for role in prompts.ROLE_INCREMENTS:
        system_prompt = prompts.build_system_prompt(role)
        persona = prompts.PERSONA_BY_ROLE[role]
        assert system_prompt.count("【团队角色：") == 1, role
        assert f"【团队角色：{labels[persona]}】" in system_prompt, role


def test_requirements_only_keeps_s03_strict_but_relaxes_other_gates() -> None:
    adjudication.set_gate_mode("requirements_only")
    try:
        with pytest.raises(adjudication.ProtocolIssue) as strict:
            adjudication.adjudicate(
                s03._RULESET,
                target_id="requirement:1",
                result_target_id="requirement:1",
                declared_decision="pass",
                checks=[],
                allowed_evidence={"requirement:1"},
            )
        assert strict.value.code == adjudication.MISSING_RESULT

        relaxed = adjudication.review_output(
            {
                "results": [
                    {
                        "target_id": "prompt:1",
                        "decision": "reject",
                        "checks": [
                            {
                                "rule_id": "G03_CONFIRMATION",
                                "outcome": "fail",
                                "evidence_ids": [],
                            }
                        ],
                    }
                ]
            },
            target_id="prompt:1",
            ruleset=adjudication.s07_rules(),
            allowed_evidence=set(),
        )
        assert relaxed.decision == "pass"
        assert relaxed.blocking_failures == ()
    finally:
        adjudication.set_gate_mode("strict")


def test_off_mode_also_relaxes_the_requirements_gate() -> None:
    adjudication.set_gate_mode("off")
    try:
        relaxed = adjudication.review_output(
            {
                "results": [
                    {
                        "target_id": "requirement:1",
                        "decision": "revise",
                        "checks": [
                            {
                                "rule_id": "G03_STRUCTURE",
                                "outcome": "fail",
                                "evidence_ids": [],
                            }
                        ],
                    }
                ]
            },
            target_id="requirement:1",
            ruleset=s03._RULESET,
            allowed_evidence=set(),
        )
        assert relaxed.decision == "pass"
        assert relaxed.approved
    finally:
        adjudication.set_gate_mode("strict")


def test_gate_mode_default_keeps_requirements_review_enabled() -> None:
    from app.config import Settings

    assert Settings.model_fields["workflow_gate_mode"].default == "requirements_only"


def test_every_stage_action_inventory_is_complete() -> None:
    for stage, module in STAGE_MODULES.items():
        assert tuple(module.ACTIONS) == STAGE_ACTIONS[stage], stage


def test_every_registered_review_catalogue_uses_one_version() -> None:
    for stage, (ruleset, blocking) in REVIEW_CATALOGUES.items():
        assert ruleset.version == adjudication.RULESET_VERSION, stage
        assert {rule.rule_id for rule in ruleset.blocking()} == blocking, stage


def test_s12_review_recovery_covers_every_artifact_consumer() -> None:
    assert s12._ARTIFACT_KIND_BY_STAGE == {
        "S02": "reference_report",
        "S03": "brief_draft",
        "S04": "creative_direction",
        "S05": "visual_plan",
        "S07": "prompt_plan",
        "S09": "quality_report",
        "S10": "delivery_manifest",
    }
    for stage in ("S01", "S02", "S03", "S04", "S05", "S07", "S09", "S10"):
        assert callable(getattr(s12, f"_resume_{stage.lower()}_review", None)), stage
    # S11 has no deferred review: its normal historical action is re-entrant.
    assert "review_legacy_item" in s11.ACTIONS


def _fail_first_reviewer(ids: Any) -> Any:
    base = _role_responder(ids)
    pending = {"value": True}

    def responder(request: Any) -> dict[str, Any]:
        task = request.payload.get("task", {})
        if task.get("role") == "output_reviewer" and pending["value"]:
            pending["value"] = False
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        return base(request)

    return responder


async def test_s10_off_gate_skips_reviewer_and_approves_manifest(
    full_settings: Any,
) -> None:
    from tests.support.adapters import LLMStub

    settings = full_settings.model_copy(update={"workflow_gate_mode": "requirements_only"})
    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> Any:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(settings, stub, seed) as env:
        prepared = await _post_action(env, "prepare_delivery")
        assert prepared.status_code == 200, prepared.text
        view = prepared.json()
        assert view["stage"] == "S10"
        assert view["status"] == "needs_input"
        assert view["issue"]["code"] == "DELIVERY_MANIFEST_READY"
        manifest = next(
            item for item in view["artifacts"] if item["kind"] == "delivery_manifest"
        )
        assert manifest["status"] == "approved"
        approval = next(
            item
            for item in view["approvals"]
            if item["approval_type"] == "delivery_manifest"
        )
        assert approval["artifact_id"] == manifest["id"]
        roles = [call.payload["task"]["role"] for call in stub.role_calls_received]
        assert roles == ["delivery_manager"]

        approved = await env.client.post(
            f"/api/projects/{env.ids.project_id}/workflow/actions/approve_delivery",
            json={"expected_revision": view["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["issue"]["code"] == "DELIVERY_APPROVED"


async def test_s10_continue_after_review_outage_skips_reviewer_when_gate_off(
    full_settings: Any,
) -> None:
    from tests.support.adapters import LLMStub

    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> Any:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _fail_first_reviewer(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        failed = await _post_action(env, "prepare_delivery")
        assert failed.status_code == 503, failed.text
        view = (
            await env.client.get(f"/api/projects/{env.ids.project_id}/workflow")
        ).json()
        assert view["stage"] == "S10"
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        manifest = next(
            item for item in view["artifacts"] if item["kind"] == "delivery_manifest"
        )
        assert manifest["status"] == "candidate"
        calls_before = len(stub.role_calls_received)

        env.settings.workflow_gate_mode = "requirements_only"
        continued = await env.client.post(
            f"/api/projects/{env.ids.project_id}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S10"
        assert recovered["status"] == "needs_input"
        assert recovered["issue"]["code"] == "DELIVERY_MANIFEST_READY"
        recovered_manifest = next(
            item
            for item in recovered["artifacts"]
            if item["kind"] == "delivery_manifest"
        )
        assert recovered_manifest["id"] == manifest["id"]
        assert recovered_manifest["status"] == "approved"
        assert any(
            item["approval_type"] == "delivery_manifest"
            for item in recovered["approvals"]
        )
        assert len(stub.role_calls_received) == calls_before

async def test_s10_recovery_replays_persisted_manifest_without_new_producer(
    full_settings: Any,
) -> None:
    import uuid

    from app.adapters.base import ProviderOutcome
    from tests.support.adapters import LLMStub, VisualStub
    from tests.support.t02_prompt import final_click, ready_project
    from tests.support.t02_prompt import responder as prompt_responder
    from tests.test_t65_generation_cycle import _drain, _environment
    from tests.test_v2_t40_prompt_review import png_bytes

    stub = LLMStub()
    pending = {"value": True}

    def responder(request: Any) -> Any:
        task = request.payload.get("task", {})
        if (
            task.get("role") == "output_reviewer"
            and task.get("stage") == "S10"
            and pending["value"]
        ):
            pending["value"] = False
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        return prompt_responder(request)

    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, stub, visual, responder) as (app, client):
        setup = await ready_project(app, client)
        clicked = await final_click(client, setup, command_id=str(uuid.uuid4()))
        assert clicked.status_code == 200, clicked.text
        await _drain(app)
        view = (await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )).json()
        if view["stage"] == "S09":
            reviewed = await client.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/review_candidate",
                json={"expected_revision": view["project_revision"]},
            )
            assert reviewed.status_code == 200, reviewed.text
            view = reviewed.json()

        assert view["stage"] == "S10"
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        manifest = next(
            item for item in view["artifacts"] if item["kind"] == "delivery_manifest"
        )
        assert manifest["status"] == "candidate"
        assert not any(
            item["approval_type"] == "delivery_manifest" for item in view["approvals"]
        )
        roles = [call.payload["task"]["role"] for call in stub.role_calls_received]
        assert roles[-2:] == ["delivery_manager", "output_reviewer"]

        continued = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S10"
        assert recovered["status"] == "needs_input"
        assert recovered["issue"]["code"] == "DELIVERY_MANIFEST_READY"
        recovered_manifest = next(
            item for item in recovered["artifacts"] if item["kind"] == "delivery_manifest"
        )
        assert recovered_manifest["id"] == manifest["id"]
        assert recovered_manifest["status"] == "approved"
        approval = next(
            item
            for item in recovered["approvals"]
            if item["approval_type"] == "delivery_manifest"
        )
        assert approval["artifact_id"] == manifest["id"]
        assert approval["approved_by"] == "output_reviewer"
        roles = [call.payload["task"]["role"] for call in stub.role_calls_received]
        assert roles[-3:] == ["delivery_manager", "output_reviewer", "output_reviewer"]
        asset_id = recovered_manifest["payload"]["asset_id"]

        hidden = await client.get(f"/api/assets/{asset_id}")
        assert hidden.status_code == 409, hidden.text

        approved = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/approve_delivery",
            json={"expected_revision": recovered["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        published = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/publish_delivery",
            json={"expected_revision": approved.json()["project_revision"]},
        )
        assert published.status_code == 200, published.text
        visible = await client.get(f"/api/assets/{asset_id}?download=true")
        assert visible.status_code == 200, visible.text
        assert visible.content


async def test_s10_recovery_invalidates_approval_before_reviewer_retry(
    full_settings: Any,
) -> None:
    from tests.support.adapters import LLMStub

    stub = LLMStub()

    async def seed(settings: Any, session: Any) -> Any:
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _fail_first_reviewer(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        failed = await _post_action(env, "prepare_delivery")
        assert failed.status_code == 503, failed.text
        view = (
            await env.client.get(f"/api/projects/{env.ids.project_id}/workflow")
        ).json()
        calls_before = len(stub.role_calls_received)

        async with env.app.state.session_factory() as session:
            approval = await session.get(Approval, env.ids.approval_id)
            assert approval is not None
            approval.revoked_at = format_timestamp(utc_now())
            approval.revocation_reason = "test-revoked"
            await session.commit()

        continued = await env.client.post(
            f"/api/projects/{env.ids.project_id}/workflow/actions/continue_workflow",
            json={"expected_revision": view["project_revision"]},
        )
        assert continued.status_code == 409, continued.text
        assert "IMAGE_NOT_APPROVED" in continued.text
        assert len(stub.role_calls_received) == calls_before
        failed_view = (
            await env.client.get(f"/api/projects/{env.ids.project_id}/workflow")
        ).json()
        assert failed_view["stage"] == "S10"
        assert failed_view["issue"]["code"] == "IMAGE_NOT_APPROVED"
        manifest = next(
            item
            for item in failed_view["artifacts"]
            if item["kind"] == "delivery_manifest"
        )
        assert manifest["status"] == "candidate"
        assert not any(
            item["approval_type"] == "delivery_manifest"
            for item in failed_view["approvals"]
        )
