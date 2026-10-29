from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from PIL import Image

from app.adapters.base import (
    BriefData,
    OrganizeRequest,
    OrganizeResult,
    ProviderOutcome,
)
from app.adapters.registry import AdapterRegistry
from app.config import Settings
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client


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


def generation_row(settings: Settings, generation_id: str):
    with sqlite3.connect(settings.database_path) as connection:
        return connection.execute(
            "SELECT status, error_code, error_message, result_locator_json, "
            "result_asset_id, completed_at, version_no, brief_snapshot_json "
            "FROM generations WHERE id = ?",
            (generation_id,),
        ).fetchone()


def update_generation(settings: Settings, generation_id: str, **values) -> None:
    assignments = ", ".join(f"{name} = ?" for name in values)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            f"UPDATE generations SET {assignments} WHERE id = ?",
            (*values.values(), generation_id),
        )


def update_project(settings: Settings, project_id: str, **values) -> None:
    assignments = ", ".join(f"{name} = ?" for name in values)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            f"UPDATE projects SET {assignments} WHERE id = ?",
            (*values.values(), project_id),
        )


async def make_failed_generation(client, visual: VisualStub, *, locator: str, expires_at=None):
    project = await create_confirmed_project(client)
    visual.submit_outcome = ProviderOutcome(
        state="succeeded",
        result_locator=locator,
        result_expires_at=expires_at,
    )
    visual.fetch_error = RuntimeError("remote download failed")
    created = await client.post(
        f"/api/projects/{project['id']}/generations", json=generation_payload(project)
    )
    assert created.status_code == 202, created.text
    generation = await wait_status(client, created.json()["generation"]["id"], {"failed"})
    assert generation["error"]["code"] == "RESULT_FETCH_FAILED"
    return project, generation


async def retrieve(client, generation_id: str):
    return await client.post(f"/api/generations/{generation_id}/retrieve-result", json={})


@pytest.mark.asyncio
async def test_failed_download_can_retrieve_same_version_and_downloads_real_file(full_settings):
    locator = "https://signed.example/result?token=top-secret"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project, failed = await make_failed_generation(client, visual, locator=locator)
        assert json.loads(generation_row(full_settings, failed["id"])[3]) == locator

        before = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert before["status"] == "failed"
        assert before["can_retrieve_result"] is True
        assert before["retrieval_unavailable_reason"] is None
        assert before["result_asset"] is None
        assert locator not in json.dumps(before)

        image = png_bytes(color=(10, 20, 30))
        visual.fetch_error = None
        visual.fetch_outcome = ProviderOutcome(state="succeeded", image_bytes=image)
        response = await retrieve(client, failed["id"])
        assert response.status_code == 200, response.text
        restored = response.json()
        assert restored["id"] == failed["id"]
        assert restored["version_no"] == 1
        assert restored["status"] == "succeeded"
        assert restored["stage"] == "done"
        assert restored["error"] is None
        assert restored["result_asset_id"]
        assert restored["result_asset"]["kind"] == "generated"
        assert restored["result_asset"]["availability"] == "available"
        assert restored["result_asset"]["mime_type"] == "image/png"
        assert restored["result_asset"]["width"] == 1024
        assert restored["result_asset"]["height"] == 1024
        assert restored["can_retrieve_result"] is False
        assert restored["retrieval_unavailable_reason"] is None
        assert visual.fetch_calls == [locator, locator]
        assert len(visual.submit_calls) == 1

        asset_id = restored["result_asset_id"]
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

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        listed = next(item for item in detail["generations"] if item["id"] == failed["id"])
        assert listed["status"] == "succeeded"
        assert listed["can_retrieve_result"] is False
        assert len(detail["generations"]) == 1

        repeated = await retrieve(client, failed["id"])
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["id"] == restored["id"]
        assert repeated.json()["version_no"] == restored["version_no"]
        assert len(visual.fetch_calls) == 2
        assert len(visual.submit_calls) == 1
        row = generation_row(full_settings, failed["id"])
        assert row[0] == "succeeded"
        assert row[1] is None
        assert row[4] == asset_id


@pytest.mark.asyncio
async def test_retrieval_stays_failed_while_waiting_and_blocks_other_write(full_settings):
    locator = "https://signed.example/slow-result"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)
        visual.fetch_error = None
        visual.fetch_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes())
        visual.fetch_gate = asyncio.Event()

        retrieval_task = asyncio.create_task(retrieve(client, failed["id"]))
        for _ in range(300):
            if len(visual.fetch_calls) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(visual.fetch_calls) == 2

        during = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert during["status"] == "failed"
        assert during["result_asset"] is None
        assert generation_row(full_settings, failed["id"])[0] == "failed"

        conflict = await retrieve(client, failed["id"])
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "PROJECT_BUSY"

        visual.fetch_gate.set()
        completed = await asyncio.wait_for(retrieval_task, timeout=5)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_retrieval_failure_keeps_original_failed_record_and_safe_error(full_settings):
    locator = "https://signed.example/private-result?token=never-log"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)
        before = generation_row(full_settings, failed["id"])
        visual.fetch_error = RuntimeError("network boom for " + locator)

        response = await retrieve(client, failed["id"])
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "RESULT_FETCH_FAILED"
        assert locator not in response.text
        assert "token" not in response.text
        after = generation_row(full_settings, failed["id"])
        assert after == before
        assert after[0] == "failed"
        assert after[1] == "RESULT_FETCH_FAILED"
        assert after[4] is None

        readable = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert readable["status"] == "failed"
        assert readable["can_retrieve_result"] is True
        assert len(visual.submit_calls) == 1
        assert len(visual.fetch_calls) == 2


@pytest.mark.asyncio
async def test_retrieval_timeout_returns_504_and_keeps_failed(full_settings):
    settings = full_settings.model_copy(update={"visual_timeout_seconds": 1})
    locator = "https://signed.example/timeout-result"
    visual = VisualStub()
    async with environment(settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)
        visual.fetch_error = None
        visual.fetch_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes())
        visual.fetch_delay_seconds = 2.0

        response = await retrieve(client, failed["id"])
        assert response.status_code == 504, response.text
        assert response.json()["error"]["code"] == "RESULT_FETCH_TIMEOUT"
        row = generation_row(settings, failed["id"])
        assert row[0] == "failed"
        assert row[1] == "RESULT_FETCH_FAILED"
        assert row[4] is None
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_no_locator_invalid_locator_and_expired_locator_are_not_retrievable(full_settings):
    locator = "https://signed.example/result"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)

        cases = [
            (None, "定位"),
            ("{not-json", "定位"),
            (
                json.dumps(
                    {"locator": locator, "expires_at": "2000-01-01T00:00:00Z"}
                ),
                "期限",
            ),
        ]
        for raw, reason_fragment in cases:
            update_generation(
                full_settings, failed["id"], result_locator_json=raw, result_asset_id=None
            )
            view = (await client.get(f"/api/generations/{failed['id']}")).json()
            assert view["can_retrieve_result"] is False
            assert reason_fragment in view["retrieval_unavailable_reason"]
            response = await retrieve(client, failed["id"])
            assert response.status_code == 409, response.text
            assert response.json()["error"]["code"] == "RESULT_NOT_RETRIEVABLE"
        assert len(visual.fetch_calls) == 1
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_changed_visual_configuration_disables_retrieval(full_settings):
    locator = "https://signed.example/config-result"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)

    changed = full_settings.model_copy(update={"visual_model": "different-model"})
    async with environment(changed, visual=VisualStub()) as (client, _registry, _llm, _visual):
        view = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert view["can_retrieve_result"] is False
        assert "配置" in view["retrieval_unavailable_reason"]
        response = await retrieve(client, failed["id"])
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "RETRIEVE_CONFIG_CHANGED"
        assert view["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_visual_adapter_is_reported_and_retrieval_returns_503(full_settings):
    locator = "https://signed.example/no-adapter"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)

    llm = LLMStub(responder=review_responder())
    registry = AdapterRegistry()
    registry.register_llm(llm.adapter_id, lambda role, http: llm)
    async with run_client(full_settings, registry) as client:
        view = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert view["can_retrieve_result"] is False
        assert "适配器" in view["retrieval_unavailable_reason"]
        response = await retrieve(client, failed["id"])
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "ADAPTER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_active_generation_and_organizing_project_block_retrieval(full_settings):
    locator = "https://signed.example/busy-result"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project, failed = await make_failed_generation(client, visual, locator=locator)

        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes())
        visual.fetch_error = None
        visual.gate = asyncio.Event()
        second = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert second.status_code == 202, second.text
        second_id = second.json()["generation"]["id"]
        for _ in range(300):
            current = (await client.get(f"/api/generations/{second_id}")).json()
            if current["status"] in {"queued", "running"}:
                break
            await asyncio.sleep(0.01)
        assert current["status"] in {"queued", "running"}

        view = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert view["can_retrieve_result"] is False
        assert "生成" in view["retrieval_unavailable_reason"]
        blocked = await retrieve(client, failed["id"])
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "PROJECT_BUSY"

        visual.gate.set()
        await wait_status(client, second_id, {"succeeded"})
        assert generation_row(full_settings, failed["id"])[0] == "failed"

        update_project(full_settings, project["id"], brief_status="organizing")
        organizing = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert organizing["can_retrieve_result"] is False
        assert "整理" in organizing["retrieval_unavailable_reason"]
        blocked = await retrieve(client, failed["id"])
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "PROJECT_BUSY"


@pytest.mark.asyncio
async def test_non_failed_record_and_empty_body_validation(full_settings):
    locator = "https://signed.example/non-failed"
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        _project, failed = await make_failed_generation(client, visual, locator=locator)
        update_generation(
            full_settings,
            failed["id"],
            status="unknown",
            error_code="VISUAL_OUTCOME_UNKNOWN",
            error_message="生图结果未确认",
        )
        view = (await client.get(f"/api/generations/{failed['id']}")).json()
        assert view["status"] == "unknown"
        assert view["can_retrieve_result"] is False
        assert "失败" in view["retrieval_unavailable_reason"]
        response = await retrieve(client, failed["id"])
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "RESULT_NOT_RETRIEVABLE"

        update_generation(
            full_settings,
            failed["id"],
            status="failed",
            error_code="RESULT_FETCH_FAILED",
            error_message="结果文件读取失败",
        )
        empty = await client.post(f"/api/generations/{failed['id']}/retrieve-result")
        assert empty.status_code == 422, empty.text
        invalid = await client.post(
            f"/api/generations/{failed['id']}/retrieve-result", json={"extra": True}
        )
        assert invalid.status_code == 422, invalid.text
        assert len(visual.fetch_calls) == 1


@pytest.mark.asyncio
async def test_existing_success_is_preserved_when_newer_retrieval_fails(full_settings):
    visual = VisualStub()
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes())
        first = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert first.status_code == 202, first.text
        first_done = await wait_status(client, first.json()["generation"]["id"], {"succeeded"})
        first_asset = first_done["result_asset_id"]
        assert first_asset

        locator = "https://signed.example/newer-failure"
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", result_locator=locator
        )
        visual.fetch_error = RuntimeError("download failed")
        second = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert second.status_code == 202, second.text
        failed = await wait_status(client, second.json()["generation"]["id"], {"failed"})
        assert failed["version_no"] == 2

        response = await retrieve(client, failed["id"])
        assert response.status_code == 502, response.text
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        old = next(item for item in detail["generations"] if item["id"] == first_done["id"])
        assert old["status"] == "succeeded"
        assert old["result_asset_id"] == first_asset
        assert old["result_asset"]["availability"] == "available"
        preview = await client.get(f"/api/assets/{first_asset}")
        assert preview.status_code == 200
        assert len(visual.submit_calls) == 2
