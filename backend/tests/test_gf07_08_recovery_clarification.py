from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.adapters.base import ProviderError
from app.db import write_transaction
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from app.workflow.tables import DiagnosticEvent
from tests.test_v2_t15_recovery import (
    RecoveryScript,
    _act,
    _create_project,
    _environment,
    _view,
)


def _foreign_review(target_id: str) -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": "pass",
                    "checks": [
                        {
                            "rule_id": rule,
                            "outcome": "pass",
                            "evidence_ids": ["foreign-evidence"],
                            "note": "",
                        }
                        for rule in REVIEW_RULE_IDS
                    ],
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


class ForeignEvidenceScript:
    """Fail the first brief review, then cite evidence from the tampered history."""

    def __init__(self) -> None:
        self.inner = RecoveryScript(fail_brief_review=True)
        self.brief_review_calls = 0

    def __call__(self, request: Any) -> Any:
        task = request.payload["task"]
        if task.get("role") == "output_reviewer":
            target = request.payload["review_targets"][0]
            if target.get("target_kind") == "brief_draft":
                self.brief_review_calls += 1
                if self.brief_review_calls == 1:
                    raise ProviderError(
                        "review timeout",
                        evidence="uncertain",
                        error_code="LLM_TIMEOUT",
                    )
                return _foreign_review(str(target["target_id"]))
        return self.inner(request)


async def _checkpoint_id(app: Any, project_id: str) -> str:
    async with app.state.session_factory() as session:
        events = list(
            (
                await session.scalars(
                    select(DiagnosticEvent)
                    .where(
                        DiagnosticEvent.project_id == project_id,
                        DiagnosticEvent.event == "recovery_checkpoint",
                    )
                    .order_by(DiagnosticEvent.created_at.desc())
                )
            ).all()
        )
    assert events, "recovery checkpoint was not persisted"
    return events[0].id


async def test_recovery_clarification_uses_server_evidence_not_tampered_history(
    full_settings,
) -> None:
    script = ForeignEvidenceScript()
    async with _environment(full_settings, script) as (app, client, stub, visual):
        project = await _create_project(client)
        first = await _act(
            client,
            project["id"],
            "submit_request",
            text="做一张米色托特包商品主图",
            expected_revision=0,
        )
        assert first.status_code == 200, first.text
        started = first.json()
        failed = await _act(
            client,
            project["id"],
            "compose_brief",
            expected_revision=started["project_revision"],
        )
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        view = await _view(client, project["id"])

        event_id = await _checkpoint_id(app, project["id"])
        async with write_transaction(app.state.session_factory) as session:
            event = await session.get(DiagnosticEvent, event_id)
            assert event is not None
            detail = json.loads(event.detail_json)
            request = json.loads(detail["request_json"])
            request["payload"]["task"]["allowed_evidence_ids"] = ["foreign-evidence"]
            detail["request_json"] = json.dumps(
                request, ensure_ascii=False, sort_keys=True
            )
            event.detail_json = json.dumps(detail, ensure_ascii=False, sort_keys=True)
            await session.flush()

        continued = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=view["project_revision"],
        )
        assert continued.status_code == 409, continued.text
        assert continued.json()["error"]["code"] == "REVIEW_INVALID"

        recovered = await _view(client, project["id"])
        assert recovered["stage"] == "S03"
        assert recovered["status"] == "blocked"
        assert recovered["issue"]["code"] == "REVIEW_INVALID"
        assert recovered["prompt_preview"] is None
        assert [
            item
            for item in recovered["artifacts"]
            if item["kind"] == "brief_draft" and item["status"] == "approved"
        ] == []
        assert visual.submit_calls == []

        replayed = [
            call
            for call in stub.role_calls_received
            if call.payload["task"].get("role") == "output_reviewer"
        ][-1]
        assert "foreign-evidence" not in replayed.payload["task"]["allowed_evidence_ids"]
