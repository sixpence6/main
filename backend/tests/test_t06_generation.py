from __future__ import annotations

import asyncio
import io
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from PIL import Image

from app.adapters.base import BriefData, OrganizeRequest, OrganizeResult, ProviderOutcome
from app.config import Settings
from app.services.generations import parse_image_size
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client


def test_parse_image_size_accepts_landscape_and_rejects_noncanonical_values():
    assert parse_image_size("3840x2160") == (3840, 2160)
    assert parse_image_size("3840×2160") is None
    assert parse_image_size("3840 x 2160") is None
    assert parse_image_size("0x2160") is None


def png_bytes(width: int = 1024, height: int = 1024, color=(240, 240, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


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
async def environment(settings: Settings, visual: VisualStub | None = None):
    llm = LLMStub(responder=review_responder())
    visual = visual or VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(settings, registry) as client:
        yield client, registry, llm, visual


async def create_confirmed_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    project = response.json()
    exchange = await client.post(
        f"/api/projects/{project['id']}/messages",
        json={"content": "做一个白色马克杯的电商主图", "expected_revision": 0},
    )
    assert exchange.status_code == 200, exchange.text
    reviewed = exchange.json()["project"]
    assert reviewed["brief_status"] == "review"
    confirmed = await client.post(
        f"/api/projects/{project['id']}/confirm",
        json={"revision": reviewed["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


async def wait_status(
    client, generation_id: str, expected: set[str], timeout_seconds: float = 5.0
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last = None
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/generations/{generation_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.01)
    raise AssertionError(f"generation did not reach {expected}: {last}")


def generation_payload(project: dict, request_id: str | None = None, **overrides) -> dict:
    payload = {
        "request_id": request_id or str(uuid4()),
        "confirmed_revision": project["revision"],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_confirmed_project_generates_real_local_result(full_settings):
    image = png_bytes()
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=image))
    async with environment(full_settings, visual=visual) as (client, registry, _llm, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        response = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert response.status_code == 202, response.text
        created = response.json()["generation"]
        assert created["status"] == "queued"
        assert created["stage"] == "waiting_slot"
        assert created["request_id"] == request_id
        assert created["confirmed_revision"] == project["revision"]

        generation = await wait_status(client, created["id"], {"succeeded"})
        assert generation["version_no"] == 1
        assert generation["stage"] == "done"
        assert generation["prompt"] == project["brief"]["prompt"]
        assert generation["brief_snapshot"]["style"] == "干净明亮"
        assert generation["adapter_id"] == "stub-visual"
        assert generation["model"] == "stub-model"
        assert generation["result_asset_id"]
        assert generation["result_asset"]["kind"] == "generated"
        assert generation["result_asset"]["availability"] == "available"
        assert generation["result_asset"]["mime_type"] == "image/png"
        assert generation["result_asset"]["width"] == 1024
        assert generation["result_asset"]["height"] == 1024
        assert generation["started_at"] is not None
        assert generation["completed_at"] is not None
        assert generation["error"] is None

        asset_id = generation["result_asset_id"]
        preview = await client.get(f"/api/assets/{asset_id}")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(preview.content)) as decoded:
            assert decoded.size == (1024, 1024)

        download = await client.get(f"/api/assets/{asset_id}?download=true")
        assert download.status_code == 200
        disposition = download.headers["content-disposition"].lower()
        assert "attachment" in disposition
        assert project["id"] in disposition
        assert "v1.png" in disposition
        with Image.open(io.BytesIO(download.content)) as decoded:
            assert decoded.size == (1024, 1024)

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["brief_status"] == "confirmed"
        listed = next(item for item in detail["generations"] if item["id"] == created["id"])
        assert listed["status"] == "succeeded"
        assert listed["result_asset_id"] == asset_id
        assert len(visual.submit_calls) == 1
        assert registry.recorder.visual_builds == 1


@pytest.mark.asyncio
async def test_generation_returns_202_before_background_execution_finishes(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert response.status_code == 202, response.text
        generation_id = response.json()["generation"]["id"]

        early = (await client.get(f"/api/generations/{generation_id}")).json()
        assert early["status"] in {"queued", "running"}
        assert early["status"] != "succeeded"
        visual.gate.set()
        done = await wait_status(client, generation_id, {"succeeded"})
        assert done["result_asset"]["availability"] == "available"


@pytest.mark.asyncio
async def test_same_request_idempotent_and_double_click_submits_once(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        payload = generation_payload(project, request_id)
        first = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert first.status_code == 202, first.text
        generation_id = first.json()["generation"]["id"]

        second = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["generation"]["id"] == generation_id
        assert visual.submit_calls == []

        visual.gate.set()
        await wait_status(client, generation_id, {"succeeded"})
        third = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert third.status_code == 200, third.text
        assert third.json()["generation"]["id"] == generation_id
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_same_request_with_different_original_params_conflicts(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes())
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert first.status_code == 202, first.text
        await wait_status(client, first.json()["generation"]["id"], {"succeeded"})

        conflicting = await client.post(
            f"/api/projects/{project['id']}/generations",
            json={
                "request_id": request_id,
                "confirmed_revision": project["revision"] - 1,
            },
        )
        assert conflicting.status_code == 409, conflicting.text
        assert conflicting.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_new_request_with_stale_confirmed_revision_is_rejected(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes())
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(
                project, confirmed_revision=project["revision"] - 1
            ),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_active_generation_blocks_new_submission_but_not_same_request(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        first = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert first.status_code == 202, first.text
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"running"})

        other = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert other.status_code == 409, other.text
        assert other.json()["error"]["code"] == "ACTIVE_GENERATION_EXISTS"

        visual.gate.set()
        await wait_status(client, generation_id, {"succeeded"})


@pytest.mark.asyncio
async def test_failed_generation_is_readable_and_has_no_result_asset(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed", error_code="PROVIDER_REJECTED", error_message="provider rejected"
        )
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert response.status_code == 202
        generation = await wait_status(client, response.json()["generation"]["id"], {"failed"})
        assert generation["result_asset_id"] is None
        assert generation["result_asset"] is None
        assert generation["error"]["code"] == "PROVIDER_REJECTED"
        assert generation["stage"] == "submitting"


@pytest.mark.asyncio
async def test_unverifiable_local_result_never_fakes_success(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=b"not an image")
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert response.status_code == 202
        generation = await wait_status(client, response.json()["generation"]["id"], {"failed"})
        assert generation["result_asset_id"] is None
        assert generation["error"]["code"] == "RESULT_FETCH_FAILED"
        assert (
            await client.get(f"/api/assets/{uuid4()}")
        ).status_code == 404


@pytest.mark.asyncio
async def test_unknown_generation_requires_explicit_acknowledgement(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="unknown", remote_task_id="remote-1", error_code="VISUAL_OUTCOME_UNKNOWN"
        )
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        first = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert first.status_code == 202
        generation = await wait_status(client, first.json()["generation"]["id"], {"unknown"})
        assert generation["status"] == "unknown"
        assert generation["remote_task_id"] == "remote-1"
        assert generation["result_asset_id"] is None
        assert generation["error"]["code"] == "VISUAL_OUTCOME_UNKNOWN"

        blocked = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "UNKNOWN_ACK_REQUIRED"

        explicit_false = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, acknowledge_unknown=False),
        )
        assert explicit_false.status_code == 409
        assert explicit_false.json()["error"]["code"] == "UNKNOWN_ACK_REQUIRED"

        allowed = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, acknowledge_unknown=True),
        )
        assert allowed.status_code == 202, allowed.text


@pytest.mark.asyncio
async def test_submission_validation_and_missing_visual_configuration(full_settings, llm_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual):
        project = (await client.post("/api/projects", json={})).json()
        response = await client.post(
            f"/api/projects/{project['id']}/generations",
            json={"request_id": str(uuid4()), "confirmed_revision": 0},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BRIEF_NOT_READY"

        invalid_id = await client.post(
            f"/api/projects/{project['id']}/generations",
            json={"request_id": "not-a-uuid", "confirmed_revision": 0},
        )
        assert invalid_id.status_code == 422
        assert invalid_id.json()["error"]["code"] == "VALIDATION_ERROR"
        confirmed_for_later = await create_confirmed_project(client)

    async with environment(llm_settings) as (client, _registry, _llm, _visual):
        response = await client.post(
            f"/api/projects/{confirmed_for_later['id']}/generations",
            json=generation_payload(confirmed_for_later),
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_generation_snapshot_survives_later_brief_edit(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes())
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        response = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        generation_id = response.json()["generation"]["id"]
        await wait_status(client, generation_id, {"succeeded"})

        edited = await client.patch(
            f"/api/projects/{project['id']}/brief",
            json={"expected_revision": project["revision"], "style": "深色背景"},
        )
        assert edited.status_code == 200, edited.text
        aged = await wait_status(client, generation_id, {"succeeded"})
        assert aged["brief_snapshot"]["style"] == "干净明亮"
        assert aged["prompt"] == project["brief"]["prompt"]


@pytest.mark.asyncio
async def test_generation_execution_is_globally_serialized_across_projects(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        first_project = await create_confirmed_project(client)
        second_project = await create_confirmed_project(client)
        first = await client.post(
            f"/api/projects/{first_project['id']}/generations",
            json=generation_payload(first_project),
        )
        second = await client.post(
            f"/api/projects/{second_project['id']}/generations",
            json=generation_payload(second_project),
        )
        assert first.status_code == second.status_code == 202
        first_id = first.json()["generation"]["id"]
        second_id = second.json()["generation"]["id"]
        await wait_status(client, first_id, {"running"})
        second_early = (await client.get(f"/api/generations/{second_id}")).json()
        assert second_early["status"] == "queued"
        assert len(visual.submit_calls) == 1
        assert visual.max_active_count == 1

        visual.gate.set()
        await wait_status(client, first_id, {"succeeded"})
        await wait_status(client, second_id, {"succeeded"})
        assert len(visual.submit_calls) == 2
        assert visual.max_active_count == 1
