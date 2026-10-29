from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.adapters.base import BriefData, OrganizeRequest, OrganizeResult
from app.config import Settings
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client


def review_brief(request: OrganizeRequest, **overrides) -> BriefData:
    values = {
        "product_description": "白色陶瓷马克杯，容量 350ml",
        "purpose": "电商主图",
        "style": "干净明亮",
        "composition": "居中构图",
        "text_content": "限时 9 折",
        "image_size": request.brief.image_size,
        "reference_asset_id": None,
        "prompt": "一只白色陶瓷马克杯放在浅色木桌上，柔和自然光，居中构图，电商主图",
    }
    values.update(overrides)
    return BriefData(**values)


def review_responder(**overrides):
    def respond(request: OrganizeRequest) -> OrganizeResult:
        return OrganizeResult(
            outcome="review",
            assistant_message="需求已整理完成，请确认。",
            brief=review_brief(request, **overrides),
        )

    return respond


@asynccontextmanager
async def environment(settings: Settings, responder=None, visual: VisualStub | None = None):
    llm = LLMStub(responder=responder or review_responder())
    visual = visual or VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(settings, registry) as client:
        yield client, registry, llm, visual


async def create_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201
    return response.json()


async def send(client, project_id: str, content: str, expected_revision: int) -> dict:
    response = await client.post(
        f"/api/projects/{project_id}/messages",
        json={"content": content, "expected_revision": expected_revision},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def review_ready(client) -> dict:
    project = await create_project(client)
    exchange = await send(client, project["id"], "做一个白色马克杯的电商主图", 0)
    assert exchange["project"]["brief_status"] == "review"
    return exchange["project"]


@pytest.mark.asyncio
async def test_patch_saves_editable_fields_and_bumps_revision(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        project = await create_project(client)
        response = await client.patch(
            f"/api/projects/{project['id']}/brief",
            json={"expected_revision": 0, "product_description": "白色马克杯", "purpose": "主图"},
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["revision"] == 1
        assert updated["brief_status"] == "draft"
        assert updated["prompt_revision"] is None
        assert updated["confirmed_revision"] is None
        assert updated["brief"]["product_description"] == "白色马克杯"
        assert updated["brief"]["purpose"] == "主图"
        assert updated["updated_at"] != project["updated_at"]


@pytest.mark.asyncio
async def test_patch_same_value_is_noop(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        project = await create_project(client)
        first = await client.patch(
            f"/api/projects/{project['id']}/brief",
            json={"expected_revision": 0, "style": "干净明亮"},
        )
        assert first.status_code == 200
        saved = first.json()
        again = await client.patch(
            f"/api/projects/{project['id']}/brief",
            json={"expected_revision": saved["revision"], "style": "干净明亮"},
        )
        assert again.status_code == 200
        assert again.json()["revision"] == saved["revision"]
        assert again.json()["updated_at"] == saved["updated_at"]


@pytest.mark.asyncio
async def test_patch_rejects_invalid_payloads(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        project = await create_project(client)
        base = f"/api/projects/{project['id']}/brief"
        cases = [
            {"expected_revision": 0},
            {"expected_revision": 0, "style": None},
            {"expected_revision": 0, "image_size": "1024x1024"},
            {"expected_revision": 0, "prompt": "x"},
            {"expected_revision": 0, "style": "x" * 8001},
            {"expected_revision": "0", "style": "干净"},
            {"style": "干净"},
        ]
        for payload in cases:
            response = await client.patch(base, json=payload)
            assert response.status_code == 422, (payload, response.text)
            assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_patch_revision_conflict(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        project = await create_project(client)
        response = await client.patch(
            f"/api/projects/{project['id']}/brief",
            json={"expected_revision": 3, "style": "干净"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_patch_after_review_clears_associations_but_keeps_old_prompt(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        reviewed = await review_ready(client)
        old_prompt = reviewed["brief"]["prompt"]
        response = await client.patch(
            f"/api/projects/{reviewed['id']}/brief",
            json={"expected_revision": reviewed["revision"], "style": "深色背景"},
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["revision"] == reviewed["revision"] + 1
        assert updated["brief_status"] == "draft"
        assert updated["prompt_revision"] is None
        assert updated["confirmed_revision"] is None
        assert updated["brief"]["style"] == "深色背景"
        assert updated["brief"]["prompt"] == old_prompt
        detail = (await client.get(f"/api/projects/{reviewed['id']}")).json()
        assert detail["messages"][-1]["brief_snapshot"]["style"] == "干净明亮"


@pytest.mark.asyncio
async def test_confirm_requires_ready_prompt(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        project = await create_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/confirm", json={"revision": 0}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BRIEF_NOT_READY"


@pytest.mark.asyncio
async def test_confirm_success_is_idempotent_and_makes_no_model_calls(full_settings):
    llm = LLMStub(responder=review_responder())
    visual = VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(full_settings, registry) as client:
        reviewed = await review_ready(client)
        assert len(llm.calls) == 1
        response = await client.post(
            f"/api/projects/{reviewed['id']}/confirm", json={"revision": reviewed["revision"]}
        )
        assert response.status_code == 200, response.text
        confirmed = response.json()
        assert confirmed["brief_status"] == "confirmed"
        assert confirmed["confirmed_revision"] == reviewed["revision"]
        assert confirmed["revision"] == reviewed["revision"]
        repeat = await client.post(
            f"/api/projects/{reviewed['id']}/confirm", json={"revision": reviewed["revision"]}
        )
        assert repeat.status_code == 200
        assert repeat.json() == confirmed
        assert len(llm.calls) == 1
        assert visual.submit_calls == []
        assert registry.recorder.visual_builds == 0


@pytest.mark.asyncio
async def test_confirm_rejects_stale_revision(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        reviewed = await review_ready(client)
        response = await client.post(
            f"/api/projects/{reviewed['id']}/confirm", json={"revision": reviewed["revision"] - 1}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_confirm_after_edit_requires_new_review(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        reviewed = await review_ready(client)
        edited = (
            await client.patch(
                f"/api/projects/{reviewed['id']}/brief",
                json={"expected_revision": reviewed["revision"], "style": "深色背景"},
            )
        ).json()
        response = await client.post(
            f"/api/projects/{reviewed['id']}/confirm", json={"revision": edited["revision"]}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BRIEF_NOT_READY"


@pytest.mark.asyncio
async def test_confirm_requires_verified_image_size(llm_settings):
    async with environment(llm_settings) as (client, _registry, _llm, _visual):
        reviewed = await review_ready(client)
        assert reviewed["brief"]["image_size"] is None
        response = await client.post(
            f"/api/projects/{reviewed['id']}/confirm", json={"revision": reviewed["revision"]}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "IMAGE_SIZE_CHANGED"


@pytest.mark.asyncio
async def test_confirm_requires_product_subject(full_settings):
    async with environment(
        full_settings, responder=review_responder(product_description="")
    ) as (client, _registry, _llm, _visual):
        reviewed = await review_ready(client)
        response = await client.post(
            f"/api/projects/{reviewed['id']}/confirm", json={"revision": reviewed["revision"]}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BRIEF_NOT_READY"
