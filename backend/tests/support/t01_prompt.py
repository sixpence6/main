"""#113 tests: authenticated HTTP, real SQLite, controlled external adapters only."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.support.adapters import LLMStub
from tests.test_t65_generation_cycle import _responder
from tests.test_v2_t40_prompt_review import base_brief, candidate_data, engineer_response


def responder(request: Any) -> dict:
    task = request.payload["task"]
    role = task["role"]
    brief = base_brief(None)

    def ready(data):
        return {"role": role, "status": "ready", "data": data, "issues": [], "questions": []}

    if role == "intent_router":
        return ready(
            {
                "route": "image_request",
                "action": "new_request",
                "evidence_ids": [task["allowed_evidence_ids"][0]],
            }
        )
    if role == "reference_analyst":
        return ready(
            {
                "reference_usage": request.payload["input_data"]["reference_usage"],
                "observations": [
                    {
                        "kind": "color",
                        "description": "杯身为白色",
                        "confidence": "clear",
                        "evidence_ids": [task["allowed_evidence_ids"][0]],
                    }
                ],
                "texts": [],
                "uncertainties": [],
            }
        )
    if role == "requirements_consultant":
        brief["reference_asset_id"] = request.payload["input_data"].get("reference_asset_id")
        brief["reference_usage"] = "product" if brief["reference_asset_id"] else "none"
        brief["facts"][0]["evidence_ids"] = [task["allowed_evidence_ids"][0]]
        return ready(brief)
    if role == "creative_planner":
        return ready(
            {
                "direction": brief["creative_direction"],
                "selling_points": [],
                "copy_lines": brief["texts"],
            }
        )
    if role == "visual_director":
        source = request.payload["input_data"]["brief_draft"]
        treatment = "参考商品外观，不复制背景" if source.get("reference_asset_id") else "无参考图"
        return ready(
            {
                "style_keywords": brief["style_keywords"],
                "composition": brief["composition"],
                "lighting": brief["lighting"],
                "background": "纯白背景",
                "palette": ["白色"],
                "text_layout": "WARM MORNING 底部居中",
                "reference_treatment": treatment,
                "preservation_rules": brief["must_preserve"],
            }
        )
    if role == "generation_engineer":
        brief = request.payload["approved_inputs"]["final_brief"]
        data = candidate_data(brief)
        # Respond to the published model contract (not internal stages).
        ids = set(task["required_rule_ids"])
        data["requirement_coverage"] = [
            x for x in data["requirement_coverage"] if x["rule_id"] in ids
        ]
        return engineer_response(data)
    if role == "output_reviewer" and task.get("stage") != "S09":
        targets = request.payload["review_targets"]
        if len(targets) == 1:
            target = targets[0]["target_id"]
            return ready(
                {
                    "results": [
                        {
                            "target_id": target,
                            "decision": "pass",
                            "checks": [
                                {
                                    "rule_id": rule,
                                    "outcome": "pass",
                                    "evidence_ids": task["allowed_evidence_ids"],
                                    "note": "",
                                }
                                for rule in task["required_rule_ids"]
                            ],
                        }
                    ]
                }
            )
    return _responder(request)


async def action(client, project_id, name, **payload):
    view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
    body = {"expected_revision": view["project_revision"], **payload}
    result = await client.post(f"/api/projects/{project_id}/workflow/actions/{name}", json=body)
    assert result.status_code == 200, result.text
    return result.json()


async def prepared(client, reference_bytes=None):
    project = (await client.post("/api/projects", json={})).json()
    project_id = project["id"]
    if reference_bytes is not None:
        uploaded = await client.post(
            f"/api/projects/{project_id}/reference",
            files={"file": ("reference.png", reference_bytes, "image/png")},
            data={"expected_revision": str(project["revision"])},
        )
        assert uploaded.status_code == 200, uploaded.text
    await action(
        client,
        project_id,
        "submit_request",
        text="请制作一个白色陶瓷杯商品主图，禁止价格标签和促销文案",
    )
    for name in ("compose_brief", "plan_creative", "plan_visual", "finalize_requirements"):
        await action(client, project_id, name)
    view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
    card = next(
        x for x in view["artifacts"] if x["kind"] == "final_brief" and x["status"] == "approved"
    )
    await action(
        client,
        project_id,
        "confirm_requirements",
        payload={
            "artifact_id": card["id"],
            "artifact_digest": card["digest"],
            "requirement_revision": view["requirement_revision"],
            "request_id": "11111111-1111-4111-8111-111111111113",
        },
    )
    return project_id


# This is an external model-adapter pause, not a mock of a workflow stage.


class ReviewPause(LLMStub):
    def __init__(self):
        super().__init__()
        self.review_started = asyncio.Event()
        self.release_review = asyncio.Event()

    async def complete(self, request):
        if (
            request.payload["task"]["role"] == "output_reviewer"
            and request.payload["review_targets"][0].get("target_kind") == "prompt_plan"
        ):
            self.review_started.set()
            await self.release_review.wait()
        return await super().complete(request)
