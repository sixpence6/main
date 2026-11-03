from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.adapters.base import ProviderOutcome
from app.workflow import state
from app.workflow.snapshot import build_execution_snapshot, requirements_from_snapshot
from app.workflow.stages import s06, s09
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import _environment
from tests.test_v2_t40_prompt_review import png_bytes
from tests.test_v3_p1_t10_execution_snapshot import (
    _confirmation,
    _execution_brief,
    _execution_responder,
    _fields,
    _setup_execution_snapshot,
)


def _snapshot(*, user_locks: dict[str, str] | None = None) -> dict[str, Any]:
    brief = _execution_brief()
    return build_execution_snapshot(
        artifact_id="artifact-t11",
        artifact_digest="a" * 64,
        artifact_revision_no=1,
        project_revision=2,
        requirement_revision=2,
        reference_asset_id=None,
        reference_usage="none",
        referenced_material_digest="b" * 64,
        brief=brief,
        user_locks=user_locks,
    )


def test_default_strength_separates_confirmed_mandates_from_model_suggestions() -> None:
    snapshot = _snapshot()
    fields = _fields(snapshot)

    assert snapshot["snapshot_schema_version"] == "1.1.0"
    assert snapshot["rules_version"] == "v3.rules.2"
    for path in (
        "product_description",
        "facts",
        "texts",
        "must_include",
        "must_preserve",
        "forbidden",
        "size",
        "count",
    ):
        assert fields[path]["strength"] == "hard", path
    for path in (
        "purpose",
        "creative_direction",
        "style_keywords",
        "composition",
        "lighting",
        "background",
        "palette",
        "text_layout",
        "reference_treatment",
        "preservation_rules",
        "copy_lines",
    ):
        assert fields[path]["strength"] == "soft", path
        assert fields[path]["user_lock_basis"] is None, path
    assert fields["assumptions"]["executable"] is False
    assert fields["assumptions"]["strength"] == "not_applicable"

    constraints = {item["path"]: item for item in snapshot["constraints"]}
    assert constraints["style_keywords"]["strength"] == "soft"
    assert constraints["must_include"]["strength"] == "hard"
    assert constraints["must_include"]["source"] == "brief_draft"
    assert constraints["must_include"]["original_value"] == _execution_brief()["must_include"][0]


def test_explicit_user_lock_upgrades_only_that_soft_field_with_basis() -> None:
    snapshot = _snapshot(user_locks={"style_keywords": "user explicitly locked minimal style"})
    fields = _fields(snapshot)
    constraints = {item["path"]: item for item in snapshot["constraints"]}

    assert snapshot["user_locks"] == {"style_keywords": "user explicitly locked minimal style"}
    assert fields["style_keywords"]["strength"] == "hard"
    assert fields["style_keywords"]["user_lock_basis"] == "user explicitly locked minimal style"
    assert constraints["style_keywords"]["strength"] == "hard"
    style = constraints["style_keywords"]
    assert style["user_lock_basis"] == "user explicitly locked minimal style"
    assert fields["background"]["strength"] == "soft"
    assert fields["composition"]["strength"] == "soft"

    rules = {item["rule_id"] for item in requirements_from_snapshot(snapshot)}
    assert "hard:style" in rules
    assert "hard:background" not in rules
    assert "hard:composition" not in rules
    assert "hard:preservation_rules" not in rules


def test_s09_ignores_soft_visual_preferences_and_keeps_explicit_visual_lock_hard() -> None:
    soft = _snapshot()
    soft_rules = s09._rule_specs(True, soft)
    assert not any(rule.rule_id == "hard:composition" and rule.applicable for rule in soft_rules)
    assert s09._composition_requirement(soft, "fallback") == "fallback"

    locked = _snapshot(user_locks={"style_keywords": "user explicitly locked minimal style"})
    locked_rules = {rule.rule_id: rule for rule in s09._rule_specs(True, locked)}
    assert locked_rules["hard:composition"].applicable is True
    requirement = s09._composition_requirement(locked, "fallback")
    expected = _execution_brief()
    assert expected["style_keywords"][0] in requirement
    assert expected["background"] not in requirement


@pytest.mark.asyncio
async def test_http_confirmation_records_locks_and_rejects_changed_strength(full_settings) -> None:
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, _execution_responder) as (app, client):
        setup = await _setup_execution_snapshot(app, client)
        base_payload = {
            "artifact_id": setup["final_artifact_id"],
            "artifact_digest": setup["final_digest"],
            "requirement_revision": setup["revision"],
        }
        first = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {**base_payload, "request_id": str(uuid.uuid4())},
            },
        )
        assert first.status_code == 200, first.text
        view = first.json()
        assert view["stage"] == "S07"
        confirmation = _confirmation(view)
        assert confirmation["user_locks"] == {}
        assert _fields(confirmation)["style_keywords"]["strength"] == "soft"
        assert _fields(confirmation)["background"]["strength"] == "soft"

        ctx = await state.load_context(app, setup["project_id"])
        approval = await state.latest_approval(ctx, "requirements_card")
        artifact = await state.latest_artifact(ctx, "final_brief", status="approved")
        assert approval is not None and artifact is not None
        assert await s06._matches_existing(ctx, approval, artifact, setup["brief"])
        assert not await s06._matches_existing(
            ctx,
            approval,
            artifact,
            setup["brief"],
            {"style_keywords": "user explicitly locked minimal style"},
        )

        after = (await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )).json()
        active = [
            item
            for item in after["approvals"]
            if item["approval_type"] == "requirements_card" and item["revoked_at"] is None
        ]
        assert len(active) == 1
        assert active[0]["checks"]["confirmation"]["user_locks"] == {}

        changed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": after["project_revision"],
                "payload": {
                    **base_payload,
                    "request_id": str(uuid.uuid4()),
                    "user_locks": {
                        "style_keywords": "user explicitly locked minimal style"
                    },
                },
            },
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["error"]["code"] == "REQUIREMENTS_CHANGED"
        revoked = (await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )).json()
        assert [
            item
            for item in revoked["approvals"]
            if item["approval_type"] == "requirements_card" and item["revoked_at"] is None
        ] == []
        assert revoked["prompt_preview"] is None
        assert visual.submit_calls == []
