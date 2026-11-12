from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from PIL import Image

from app.adapters.base import BriefData, OrganizeRequest, OrganizeResult, ProviderOutcome
from app.config import Settings
from app.services import generations as generation_service
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client


def png_bytes(width: int = 1024, height: int = 1024) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(output, format="PNG")
    return output.getvalue()


def review_brief(request: OrganizeRequest) -> BriefData:
    return BriefData(
        product_description="白色陶瓷马克杯，容量 350ml",
        purpose="电商主图",
        style="干净明亮",
        composition="居中构图",
        text_content="限时 9 折",
        image_size=request.brief.image_size,
        reference_asset_id=None,
        prompt="一只白色陶瓷马克杯放在浅色木桌上，柔和自然光，居中构图，电商主图",
    )


def review_responder():
    def respond(request: OrganizeRequest) -> OrganizeResult:
        return OrganizeResult(
            outcome="review",
            assistant_message="需求已整理完成，请确认。",
            brief=review_brief(request),
        )

    return respond


@asynccontextmanager
async def environment(settings: Settings, visual: VisualStub | None = None):
    llm = LLMStub(responder=review_responder())
    visual = visual or VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(settings, registry) as client:
        yield client, visual


async def create_confirmed_project(client) -> dict:
    project = (await client.post("/api/projects", json={})).json()
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


def generation_payload(project: dict, request_id: str | None = None, **overrides) -> dict:
    payload = {
        "request_id": request_id or str(uuid4()),
        "confirmed_revision": project["revision"],
    }
    payload.update(overrides)
    return payload


async def wait_status(client, generation_id: str, expected: set[str], timeout_seconds=5.0) -> dict:
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


def canonical_for(project: dict, request_id: str, acknowledge: object = ...) -> str:
    payload = {
        "project_id": project["id"],
        "request_id": request_id,
        "confirmed_revision": project["revision"],
        "acknowledge_unknown_present": acknowledge is not ...,
    }
    if acknowledge is not ...:
        payload["acknowledge_unknown"] = acknowledge
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def read_generation_row(settings: Settings, generation_id: str):
    with sqlite3.connect(settings.database_path) as connection:
        return connection.execute(
            "SELECT request_canonical, request_digest, brief_snapshot_json, prompt, model "
            "FROM generations WHERE id = ?",
            (generation_id,),
        ).fetchone()


@pytest.mark.asyncio
async def test_canonical_original_is_stored_and_changed_parameters_conflict(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert first.status_code == 202, first.text
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"succeeded"})

        row = read_generation_row(full_settings, generation_id)
        expected = canonical_for(project, request_id)
        assert row[0] == expected
        assert row[1] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
        snapshot = json.loads(row[2])
        assert snapshot["style"] == "干净明亮"
        assert row[3] == project["brief"]["prompt"]
        assert row[4] == "stub-model"

        same = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert same.status_code == 200, same.text
        assert same.json()["generation"]["id"] == generation_id

        explicit_false = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id, acknowledge_unknown=False),
        )
        assert explicit_false.status_code == 409
        assert explicit_false.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        explicit_true = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id, acknowledge_unknown=True),
        )
        assert explicit_true.status_code == 409
        assert explicit_true.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        changed_revision = await client.post(
            f"/api/projects/{project['id']}/generations",
            json={
                "request_id": request_id,
                "confirmed_revision": project["revision"] + 1,
            },
        )
        assert changed_revision.status_code == 409
        assert changed_revision.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(visual.submit_calls) == 1

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        listed = next(item for item in detail["generations"] if item["id"] == generation_id)
        assert listed["request_id"] == request_id


@pytest.mark.asyncio
async def test_explicit_false_and_omitted_acknowledgement_are_different_originals(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id, acknowledge_unknown=False),
        )
        assert first.status_code == 202, first.text
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"succeeded"})

        row = read_generation_row(full_settings, generation_id)
        assert row[0] == canonical_for(project, request_id, False)
        assert '"acknowledge_unknown":false' in row[0]

        omitted = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert omitted.status_code == 409
        assert omitted.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_concurrent_identical_requests_share_one_version_and_one_submit(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        payload = generation_payload(project, request_id)

        responses = await asyncio.gather(
            client.post(f"/api/projects/{project['id']}/generations", json=payload),
            client.post(f"/api/projects/{project['id']}/generations", json=payload),
            client.post(f"/api/projects/{project['id']}/generations", json=payload),
        )
        assert sorted(response.status_code for response in responses) == [200, 200, 202]
        ids = {response.json()["generation"]["id"] for response in responses}
        assert len(ids) == 1
        generation_id = ids.pop()

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert len(detail["generations"]) == 1
        assert detail["generations"][0]["version_no"] == 1
        assert visual.max_active_count <= 1

        visual.gate.set()
        await wait_status(client, generation_id, {"succeeded"})
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_concurrent_different_requests_keep_at_most_one_active_generation(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        first_payload = generation_payload(project)
        second_payload = generation_payload(project)

        responses = await asyncio.gather(
            client.post(f"/api/projects/{project['id']}/generations", json=first_payload),
            client.post(f"/api/projects/{project['id']}/generations", json=second_payload),
        )
        assert sorted(response.status_code for response in responses) == [202, 409]
        rejected = next(response for response in responses if response.status_code == 409)
        assert rejected.json()["error"]["code"] == "ACTIVE_GENERATION_EXISTS"

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        active = [
            generation
            for generation in detail["generations"]
            if generation["status"] in {"queued", "running"}
        ]
        assert len(active) == 1
        assert len(detail["generations"]) == 1

        visual.gate.set()
        await wait_status(client, active[0]["id"], {"succeeded"})
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_constraint_race_rolls_back_and_returns_original_record(full_settings, monkeypatch):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        payload = generation_payload(project, request_id)
        first = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert first.status_code == 202, first.text
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"succeeded"})

        real_find = generation_service._find_by_request
        calls = 0

        async def miss_duplicate_twice(session, project_id, request_id_value):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return None
            return await real_find(session, project_id, request_id_value)

        monkeypatch.setattr(generation_service, "_find_by_request", miss_duplicate_twice)
        recovered = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["generation"]["id"] == generation_id
        assert calls >= 3
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_old_original_survives_terminal_edit_and_still_has_old_snapshot(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"succeeded"})

        edited = await client.patch(
            f"/api/projects/{project['id']}/brief",
            json={"expected_revision": project["revision"], "style": "深色背景"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["brief_status"] == "draft"

        old = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert old.status_code == 200, old.text
        old_generation = old.json()["generation"]
        assert old_generation["id"] == generation_id
        assert old_generation["version_no"] == 1
        assert old_generation["brief_snapshot"]["style"] == "干净明亮"
        assert old_generation["prompt"] == project["brief"]["prompt"]
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_old_original_survives_changed_and_missing_model_configuration(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    project = None
    request_id = str(uuid4())
    generation_id = None
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"succeeded"})

    changed_settings = full_settings.model_copy(update={"visual_model": "stub-model-v2"})
    changed_visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes())
    )
    async with environment(changed_settings, visual=changed_visual) as (client, _visual):
        old = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert old.status_code == 200, old.text
        assert old.json()["generation"]["id"] == generation_id
        assert old.json()["generation"]["model"] == "stub-model"
        assert changed_visual.submit_calls == []

        replacement = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert replacement.status_code == 202, replacement.text
        assert replacement.json()["generation"]["model"] == "stub-model-v2"

    missing_settings = full_settings.model_copy(
        update={
            "visual_adapter": None,
            "visual_base_url": None,
            "visual_api_key": None,
            "visual_model": None,
            "visual_image_size": None,
        }
    )
    second_project = None
    second_request = str(uuid4())
    async with environment(full_settings, visual=visual) as (client, _visual):
        second_project = await create_confirmed_project(client)
        first_second = await client.post(
            f"/api/projects/{second_project['id']}/generations",
            json=generation_payload(second_project, second_request),
        )
        second_generation = first_second.json()["generation"]
        await wait_status(client, second_generation["id"], {"succeeded"})

    async with environment(missing_settings) as (client, _visual):
        old = await client.post(
            f"/api/projects/{second_project['id']}/generations",
            json=generation_payload(second_project, second_request),
        )
        assert old.status_code == 200, old.text
        assert old.json()["generation"]["id"] == second_generation["id"]

        new_request = await client.post(
            f"/api/projects/{second_project['id']}/generations",
            json=generation_payload(second_project),
        )
        assert new_request.status_code == 503
        assert new_request.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_old_original_wins_over_newer_active_generation(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        first_request = str(uuid4())
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, first_request),
        )
        first_id = first.json()["generation"]["id"]
        await wait_status(client, first_id, {"succeeded"})

        visual.gate = asyncio.Event()
        second = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert second.status_code == 202, second.text
        second_id = second.json()["generation"]["id"]
        await wait_status(client, second_id, {"running"})

        old = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, first_request),
        )
        assert old.status_code == 200, old.text
        assert old.json()["generation"]["id"] == first_id
        assert old.json()["generation"]["version_no"] == 1
        assert old.json()["generation"]["status"] == "succeeded"

        visual.gate.set()
        await wait_status(client, second_id, {"succeeded"})
        assert len(visual.submit_calls) == 2


@pytest.mark.asyncio
async def test_unknown_original_is_queryable_and_recovered_only_by_exact_request(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="unknown", remote_task_id="remote-t07", error_code="VISUAL_OUTCOME_UNKNOWN"
        )
    )
    async with environment(full_settings, visual=visual) as (client, _visual):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        first = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id, acknowledge_unknown=True),
        )
        assert first.status_code == 202, first.text
        generation_id = first.json()["generation"]["id"]
        await wait_status(client, generation_id, {"unknown"})

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        listed = next(item for item in detail["generations"] if item["id"] == generation_id)
        assert listed["request_id"] == request_id
        assert listed["status"] == "unknown"
        assert listed["remote_task_id"] == "remote-t07"

        recovered = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id, acknowledge_unknown=True),
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["generation"]["id"] == generation_id
        assert len(detail["generations"]) == 1

        changed_ack_presence = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, request_id),
        )
        assert changed_ack_presence.status_code == 409
        assert changed_ack_presence.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        blocked_new = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert blocked_new.status_code == 409
        assert blocked_new.json()["error"]["code"] == "UNKNOWN_ACK_REQUIRED"

        new_request = str(uuid4())
        explicit = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, new_request, acknowledge_unknown=True),
        )
        assert explicit.status_code == 202, explicit.text
        new_generation = explicit.json()["generation"]
        assert new_generation["version_no"] == 2
        await wait_status(client, new_generation["id"], {"unknown"})
        assert len(visual.submit_calls) == 2
