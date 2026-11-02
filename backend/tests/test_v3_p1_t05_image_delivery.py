from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.auth.models import Account
from app.auth.service import normalize_email
from app.db import write_transaction
from app.models import Project
from app.workflow import adjudication
from app.workflow.stages import s09, s10, s11
from tests.test_t59_isolation import _add_account, _login_user
from tests.test_v2_t12_quality import (
    _load_run,
    _post_review,
    _responder,
    _seed_project,
)
from tests.test_v2_t13_delivery import (
    _delivery_env,
    _post_action,
    _role_responder,
    _seed_ready_state,
)
from tests.test_v2_t49_legacy_review import (
    Script,
    _act,
    _approval_rows,
    _legacy,
    _workflow,
)
from tests.test_v2_t49_legacy_review import (
    _environment as _legacy_environment,
)


def _check(
    rule_id: str,
    *,
    outcome: str = "pass",
    evidence: tuple[str, ...] = ("artifact:target",),
    instance: str = "",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "outcome": outcome,
        "evidence_ids": list(evidence),
        "note": "",
        "instance": instance,
    }


def _result(target_id: str, checks: list[dict[str, Any]], decision: str = "pass") -> dict[str, Any]:
    return {"target_id": target_id, "decision": decision, "checks": checks}


def test_review_results_requires_each_expected_target_exactly_once() -> None:
    data = {"results": [_result("quality", [_check("review:quality_report")])]}
    mapping = adjudication.review_results(data, target_ids=("quality",))
    assert list(mapping) == ["quality"]

    with pytest.raises(adjudication.ProtocolIssue) as duplicate:
        adjudication.review_results(
            {"results": [_result("quality", [_check("review:quality_report")])] * 2},
            target_ids=("quality",),
        )
    assert duplicate.value.code == "WRONG_TARGET"

    with pytest.raises(adjudication.ProtocolIssue) as missing:
        adjudication.review_results(data, target_ids=("quality", "image"))
    assert missing.value.code == "MISSING_RESULT"

    with pytest.raises(adjudication.ProtocolIssue) as empty:
        adjudication.review_results(
            {"results": [{"target_id": "quality", "decision": "pass", "checks": []}]},
            target_ids=("quality",),
        )
    assert empty.value.code == "MISSING_RESULT"


def test_s09_rule_sets_separate_report_image_and_advisories() -> None:
    quality = s09._quality_ruleset()
    assert quality.target_kind == "quality_report"
    assert [rule.rule_id for rule in quality.blocking()] == ["review:quality_report"]
    assert all(rule.kind == "advisory" for rule in quality.advisory())

    image = s09._image_ruleset(s09._rule_specs(False))
    blocking = {rule.rule_id: rule for rule in image.blocking()}
    assert "hard:subject" in blocking
    assert blocking["hard:product_consistency"].applicable is False
    assert blocking["hard:product_consistency"].reason
    assert "review:scope" in {rule.rule_id for rule in image.advisory()}


def test_s10_and_s11_catalogues_keep_registered_legacy_rules() -> None:
    manifest = s10._MANIFEST_RULESET
    assert manifest.target_kind == "delivery_manifest"
    assert {rule.rule_id for rule in manifest.blocking()} == set(s10._MANIFEST_RULES)
    assert manifest.version == adjudication.RULESET_VERSION

    legacy = s11._review_ruleset("legacy_message", s11.LEGACY_TEXT_RULES)
    assert {rule.rule_id for rule in legacy.blocking()} == set(s11.LEGACY_TEXT_RULES)
    assert "review:evidence" in {rule.rule_id for rule in legacy.advisory()}


def _image_unknown_responder():
    base = _responder("pass")

    def respond(request: Any) -> dict[str, Any]:
        data = base(request)
        if request.payload["task"]["role"] != "output_reviewer":
            return data
        image_target = next(
            target
            for target in request.payload["review_targets"]
            if target["target_kind"] == "image"
        )
        for result in data["data"]["results"]:
            if result["target_id"] != image_target["target_id"]:
                continue
            result["decision"] = "unknown"
            for check in result["checks"]:
                check["outcome"] = "unknown"
        return data

    return respond


async def test_report_pass_with_image_failure_never_exposes_bytes(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("reviewer_reject")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == "S09"
    assert body["status"] == "blocked"
    assert body["issue"]["code"] == "IMAGE_NOT_APPROVED"
    report = next(item for item in body["artifacts"] if item["kind"] == "quality_report")
    assert report["status"] == "approved"
    assert not any(item["approval_type"] == "image" for item in body["approvals"])
    for suffix in ("", "?download=true"):
        denied = await llm_client.get(f"/api/assets/{seed.asset_id}{suffix}")
        assert denied.status_code in {404, 409}, denied.text
        assert seed.generated_bytes not in denied.content


async def test_report_pass_with_image_unknown_never_exposes_bytes(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _image_unknown_responder()
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "blocked"
    assert body["issue"]["code"] == "IMAGE_NOT_APPROVED"
    report = next(item for item in body["artifacts"] if item["kind"] == "quality_report")
    assert report["status"] == "approved"
    assert not any(item["approval_type"] == "image" for item in body["approvals"])
    await _assert_asset_denied(llm_client, seed.asset_id, seed.generated_bytes)


async def _assert_asset_denied(client: Any, asset_id: str, raw: bytes) -> None:
    for suffix in ("", "?download=true"):
        denied = await client.get(f"/api/assets/{asset_id}{suffix}")
        assert denied.status_code in {404, 409}, denied.text
        assert raw not in denied.content


async def test_s09_duplicate_report_rule_is_a_typed_protocol_error(
    llm_app: Any, llm_client: Any, llm_settings: Any
) -> None:
    stub = llm_app.state.registry.stub_llm
    stub.role_responder = _responder("duplicate_report_checks")
    seed = await _seed_project(llm_app, llm_settings, llm_client)

    response = await _post_review(llm_client, seed)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "REVIEW_INVALID"
    run = await _load_run(llm_app, seed.run_id)
    assert run.status == "blocked"
    assert run.issue_code == "REVIEW_INVALID"


def _manifest_revise_responder(ids):
    base = _role_responder(ids)

    def respond(request: Any) -> dict[str, Any]:
        data = base(request)
        if request.payload["task"]["role"] == "output_reviewer":
            result = data["data"]["results"][0]
            result["decision"] = "revise"
            result["checks"][0]["outcome"] = "fail"
        return data

    return respond


async def test_manifest_duplicate_rule_is_a_typed_protocol_error(full_settings: Any) -> None:
    from tests.support.adapters import LLMStub

    stub = LLMStub()

    async def seed(settings: Any, session: Any):
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids, duplicate_first_required=True)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        response = await _post_action(env, "prepare_delivery")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        run = await _load_run(env.app, env.ids.run_id)
        assert run.status == "blocked"
        assert run.issue_code == "REVIEW_INVALID"


async def test_manifest_rejection_is_not_reported_as_an_outage(full_settings: Any) -> None:
    from tests.support.adapters import LLMStub

    stub = LLMStub()

    async def seed(settings: Any, session: Any):
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _manifest_revise_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        response = await _post_action(env, "prepare_delivery")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_REJECTED"
        run = await _load_run(env.app, env.ids.run_id)
        assert run.status == "blocked"
        assert run.issue_code == "REVIEW_REJECTED"


class _UnknownLegacyRuleScript(Script):
    def _review(self, request: Any) -> dict[str, Any]:
        data = super()._review(request)
        result = data["data"]["results"][0]
        evidence = list(request.payload["task"]["allowed_evidence_ids"])[:1]
        result["checks"].append(
            {
                "rule_id": "LEGACY_INVENTED_RULE",
                "outcome": "pass",
                "evidence_ids": evidence or [result["target_id"]],
                "note": "",
            }
        )
        return data


async def test_legacy_unknown_rule_stays_isolated_and_typed(
    settings: Any, full_settings: Any
) -> None:
    script = _UnknownLegacyRuleScript()
    async with _legacy_environment(settings, full_settings, script) as (
        client,
        _stub,
        _registry,
        ids,
    ):
        item = _legacy(
            await _workflow(client, ids["project_id"]),
            "message",
            ids["assistant_message_id"],
        )
        response = await _act(client, ids["project_id"], item["id"])
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVIEW_INVALID"
        view = await _workflow(client, ids["project_id"])
        item_view = _legacy(view, "message", ids["assistant_message_id"])
        assert item_view["status"] == "legacy_unreviewed"
        assert _approval_rows(settings) == []


async def test_published_image_identity_matrix_blocks_other_users_and_admin(
    full_settings: Any,
) -> None:
    from tests.support.adapters import LLMStub

    stub = LLMStub()

    async def seed(settings: Any, session: Any):
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        await _add_account(env.app, "asset-a@example.com")
        await _add_account(env.app, "asset-b@example.com")
        async with write_transaction(env.app.state.session_factory) as session:
            account = await session.scalar(
                select(Account).where(
                    Account.email_normalized == normalize_email("asset-a@example.com")
                )
            )
            project = await session.get(Project, env.ids.project_id)
            assert account is not None and project is not None
            project.owner_account_id = account.id
        user_a = await _login_user(env.app, "asset-a@example.com")
        user_b = await _login_user(env.app, "asset-b@example.com")

        prepared = await user_a.post(
            f"/api/projects/{env.ids.project_id}/workflow/actions/prepare_delivery",
            json={"expected_revision": 1},
        )
        assert prepared.status_code == 200, prepared.text
        approved = await user_a.post(
            f"/api/projects/{env.ids.project_id}/workflow/actions/approve_delivery",
            json={"expected_revision": prepared.json()["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        published = await user_a.post(
            f"/api/projects/{env.ids.project_id}/workflow/actions/publish_delivery",
            json={"expected_revision": approved.json()["project_revision"]},
        )
        assert published.status_code == 200, published.text
        assert published.json()["delivery"]["status"] == "published"

        owner = await user_a.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert owner.status_code == 200, owner.text
        assert owner.content == env.ids.image_bytes
        for client in (user_b, env.client):
            for suffix in ("", "?download=true"):
                denied = await client.get(f"/api/assets/{env.ids.asset_id}{suffix}")
                assert denied.status_code in {403, 404}, denied.text
                assert env.ids.image_bytes not in denied.content
            workflow = await client.get(f"/api/projects/{env.ids.project_id}/workflow")
            assert workflow.status_code == 404, workflow.text
            detail = await client.get(f"/api/projects/{env.ids.project_id}")
            assert detail.status_code == 404, detail.text
