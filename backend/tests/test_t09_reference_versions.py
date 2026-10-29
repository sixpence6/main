from __future__ import annotations

import asyncio
import io
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from PIL import Image

from app.adapters.base import BriefData, OrganizeRequest, OrganizeResult, ProviderOutcome
from app.config import Settings
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client


def png_bytes(width: int = 1024, height: int = 1024, color=(240, 240, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def feedback_brief(request: OrganizeRequest) -> BriefData:
    base = request.brief
    style = "温暖柔和" if "温暖" in request.user_text else (base.style or "干净明亮")
    prompt = base.prompt or ("一只白色陶瓷马克杯放在浅色木桌上，干净明亮，居中构图，电商主图")
    if style != base.style and base.style:
        prompt = prompt.replace(base.style, style)
    return BriefData(
        product_description=base.product_description or "白色陶瓷马克杯，容量 350ml",
        purpose=base.purpose or "电商主图",
        style=style,
        composition=base.composition or "居中构图",
        text_content=base.text_content or "限时 9 折",
        image_size=base.image_size,
        reference_asset_id=base.reference_asset_id,
        prompt=prompt,
    )


def feedback_responder(request: OrganizeRequest) -> OrganizeResult:
    return OrganizeResult(
        outcome="review",
        assistant_message="需求已整理完成，请确认。",
        brief=feedback_brief(request),
    )


@asynccontextmanager
async def environment(settings: Settings, visual: VisualStub | None = None):
    visual = visual or VisualStub()
    llm = LLMStub(responder=feedback_responder)
    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(settings, registry) as client:
        yield client, llm, visual


async def create_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def upload(
    client,
    project_id: str,
    revision: int,
    data: bytes,
    *,
    filename: str = "reference.png",
    content_type: str = "image/png",
):
    return await client.post(
        f"/api/projects/{project_id}/reference",
        files={"file": (filename, data, content_type)},
        data={"expected_revision": str(revision)},
    )


async def upload_ok(client, project_id: str, revision: int, data: bytes) -> dict:
    response = await upload(client, project_id, revision, data)
    assert response.status_code == 200, response.text
    return response.json()


async def organize_and_confirm(client, project: dict, text: str) -> dict:
    exchange = await client.post(
        f"/api/projects/{project['id']}/messages",
        json={"content": text, "expected_revision": project["revision"]},
    )
    assert exchange.status_code == 200, exchange.text
    reviewed = exchange.json()["project"]
    assert reviewed["brief_status"] == "review", reviewed
    confirmed = await client.post(
        f"/api/projects/{project['id']}/confirm",
        json={"revision": reviewed["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


async def create_confirmed_with_reference(client, image: bytes) -> tuple[dict, dict]:
    project = await create_project(client)
    uploaded = await upload_ok(client, project["id"], project["revision"], image)
    confirmed = await organize_and_confirm(client, uploaded, "用这张参考图做一张电商主图")
    return confirmed, uploaded["reference_asset"]


async def create_confirmed_without_reference(client) -> dict:
    project = await create_project(client)
    return await organize_and_confirm(client, project, "做一个白色马克杯的电商主图")


async def wait_status(
    client, generation_id: str, expected: set[str], timeout_seconds: float = 5.0
) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last = None
    while loop.time() < deadline:
        response = await client.get(f"/api/generations/{generation_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.01)
    raise AssertionError(f"generation did not reach {expected}: {last}")


def generation_payload(project: dict, request_id: str | None = None) -> dict:
    return {
        "request_id": request_id or str(uuid4()),
        "confirmed_revision": project["revision"],
    }


async def submit(client, project: dict, **overrides):
    payload = generation_payload(project)
    payload.update(overrides)
    return await client.post(f"/api/projects/{project['id']}/generations", json=payload)


async def detail(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_reference_bytes_reach_visual_and_feedback_keeps_original_reference(
    full_settings,
):
    first_image = png_bytes(color=(220, 40, 40))
    second_image = png_bytes(color=(40, 60, 220))
    reference_image = png_bytes(width=640, height=480, color=(30, 160, 30))
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=first_image))
    async with environment(full_settings, visual) as (client, llm, visual):
        confirmed, reference_asset = await create_confirmed_with_reference(client, reference_image)
        project_id = confirmed["id"]
        reference_id = reference_asset["id"]

        first_submit = await submit(client, confirmed)
        assert first_submit.status_code == 202, first_submit.text
        first = await wait_status(client, first_submit.json()["generation"]["id"], {"succeeded"})
        assert first["version_no"] == 1
        assert first["reference_asset_id"] == reference_id
        assert first["brief_snapshot"]["reference_asset_id"] == reference_id
        assert first["result_asset"]["kind"] == "generated"
        assert first["result_asset_id"] != reference_id
        assert first["result_asset"]["width"] == 1024
        assert len(visual.submit_calls) == 1
        request_one = visual.submit_calls[0]
        assert request_one.project_id == project_id
        assert request_one.version_no == 1
        assert request_one.reference_mime_type == "image/png"
        assert request_one.reference_path is not None
        assert request_one.reference_path.read_bytes() == reference_image
        assert request_one.brief.reference_asset_id == reference_id

        feedback_exchange = await client.post(
            f"/api/projects/{project_id}/messages",
            json={
                "content": "把风格改成温暖柔和，其他不变",
                "expected_revision": confirmed["revision"],
            },
        )
        assert feedback_exchange.status_code == 200, feedback_exchange.text
        reviewed = feedback_exchange.json()["project"]
        assert reviewed["brief_status"] == "review"
        assert reviewed["brief"]["reference_asset_id"] == reference_id
        assert reviewed["brief"]["product_description"] == "白色陶瓷马克杯，容量 350ml"
        assert reviewed["brief"]["purpose"] == "电商主图"
        assert reviewed["brief"]["composition"] == "居中构图"
        assert reviewed["brief"]["text_content"] == "限时 9 折"
        assert reviewed["brief"]["style"] == "温暖柔和"
        assert len(llm.calls) == 2
        feedback_request = llm.calls[1]
        assert feedback_request.has_reference_image is True
        assert feedback_request.brief.reference_asset_id == reference_id
        assert feedback_request.brief.style == "干净明亮"
        assert feedback_request.brief.product_description == "白色陶瓷马克杯，容量 350ml"

        confirmed_two = await client.post(
            f"/api/projects/{project_id}/confirm",
            json={"revision": reviewed["revision"]},
        )
        assert confirmed_two.status_code == 200, confirmed_two.text
        confirmed_two = confirmed_two.json()
        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=second_image)
        second_submit = await submit(client, confirmed_two)
        assert second_submit.status_code == 202, second_submit.text
        second = await wait_status(client, second_submit.json()["generation"]["id"], {"succeeded"})
        assert second["version_no"] == 2
        assert second["reference_asset_id"] == reference_id
        assert second["brief_snapshot"]["reference_asset_id"] == reference_id
        assert second["brief_snapshot"]["style"] == "温暖柔和"
        assert len(visual.submit_calls) == 2
        request_two = visual.submit_calls[1]
        assert request_two.version_no == 2
        assert request_two.reference_path is not None
        assert request_two.reference_path.read_bytes() == reference_image
        assert request_two.brief.reference_asset_id == reference_id

        project_detail = await detail(client, project_id)
        generations = project_detail["generations"]
        assert [item["version_no"] for item in generations] == [1, 2]
        assert generations[0]["brief_snapshot"]["style"] == "干净明亮"
        assert generations[0]["prompt"] != generations[1]["prompt"]
        assert generations[0]["result_asset_id"] == first["result_asset_id"]
        assert generations[1]["result_asset_id"] == second["result_asset_id"]
        assert project_detail["project"]["reference_asset"]["id"] == reference_id

        assistant_snapshots = [
            message["brief_snapshot"]
            for message in project_detail["messages"]
            if message["role"] == "assistant" and message["brief_snapshot"]
        ]
        assert len(assistant_snapshots) == 2
        assert assistant_snapshots[0]["style"] == "干净明亮"
        assert assistant_snapshots[1]["style"] == "温暖柔和"

        for generation, expected_bytes, expected_version in (
            (first, first_image, "v1.png"),
            (second, second_image, "v2.png"),
        ):
            download = await client.get(
                f"/api/assets/{generation['result_asset_id']}?download=true"
            )
            assert download.status_code == 200
            assert download.content == expected_bytes
            assert expected_version in download.headers["content-disposition"].lower()
            assert download.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_replacing_reference_invalidates_confirmation_and_new_snapshot_uses_new_file(
    full_settings,
):
    first_image = png_bytes(color=(200, 20, 20))
    replacement_image = png_bytes(width=800, height=600, color=(20, 120, 200))
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=first_image))
    async with environment(full_settings, visual) as (client, _llm, visual):
        confirmed, old_reference = await create_confirmed_with_reference(
            client, png_bytes(width=640, height=480, color=(20, 200, 20))
        )
        project_id = confirmed["id"]
        first_submit = await submit(client, confirmed)
        first = await wait_status(client, first_submit.json()["generation"]["id"], {"succeeded"})
        old_result_id = first["result_asset_id"]
        old_reference_id = old_reference["id"]
        old_reference_bytes = await client.get(f"/api/assets/{old_reference_id}")
        assert old_reference_bytes.status_code == 200

        replaced_response = await upload(
            client, project_id, confirmed["revision"], replacement_image
        )
        assert replaced_response.status_code == 200, replaced_response.text
        replaced = replaced_response.json()
        assert replaced["revision"] == confirmed["revision"] + 1
        assert replaced["brief_status"] == "draft"
        assert replaced["prompt_revision"] is None
        assert replaced["confirmed_revision"] is None
        assert replaced["brief"]["prompt"] == confirmed["brief"]["prompt"]
        new_reference_id = replaced["reference_asset"]["id"]
        assert new_reference_id != old_reference_id

        blocked = await submit(client, replaced)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "BRIEF_NOT_READY"
        assert len(visual.submit_calls) == 1

        confirmed_two = await organize_and_confirm(
            client, replaced, "参考图已替换，请按当前需求重新整理"
        )
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(color=(20, 120, 200))
        )
        second_submit = await submit(client, confirmed_two)
        assert second_submit.status_code == 202, second_submit.text
        second = await wait_status(client, second_submit.json()["generation"]["id"], {"succeeded"})
        assert second["version_no"] == 2
        assert second["reference_asset_id"] == new_reference_id
        assert second["brief_snapshot"]["reference_asset_id"] == new_reference_id
        assert len(visual.submit_calls) == 2
        assert visual.submit_calls[1].reference_path is not None
        assert visual.submit_calls[1].reference_path.read_bytes() == replacement_image

        project_detail = await detail(client, project_id)
        old_generation = project_detail["generations"][0]
        assert old_generation["version_no"] == 1
        assert old_generation["reference_asset_id"] == old_reference_id
        assert old_generation["result_asset_id"] == old_result_id
        old_download = await client.get(f"/api/assets/{old_result_id}?download=true")
        assert old_download.status_code == 200
        assert old_download.content == first_image
        new_generation = project_detail["generations"][1]
        assert new_generation["reference_asset_id"] == new_reference_id
        assert project_detail["project"]["reference_asset"]["id"] == new_reference_id


@pytest.mark.asyncio
async def test_removing_reference_enables_text_only_generation_and_keeps_old_history(
    full_settings,
):
    first_image = png_bytes(color=(120, 30, 180))
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=first_image))
    async with environment(full_settings, visual) as (client, _llm, visual):
        confirmed, reference_asset = await create_confirmed_with_reference(
            client, png_bytes(color=(200, 120, 30))
        )
        project_id = confirmed["id"]
        first_submit = await submit(client, confirmed)
        first = await wait_status(client, first_submit.json()["generation"]["id"], {"succeeded"})

        removed = await client.delete(
            f"/api/projects/{project_id}/reference?expected_revision={confirmed['revision']}"
        )
        assert removed.status_code == 200, removed.text
        removed_project = removed.json()
        assert removed_project["reference_asset"] is None
        assert removed_project["brief_status"] == "draft"
        assert removed_project["prompt_revision"] is None
        assert removed_project["confirmed_revision"] is None
        assert removed_project["brief"]["prompt"] == confirmed["brief"]["prompt"]

        confirmed_two = await organize_and_confirm(
            client, removed_project, "移除参考图，按纯文字需求重新生成"
        )
        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(color=(30, 180, 120))
        )
        second_submit = await submit(client, confirmed_two)
        assert second_submit.status_code == 202, second_submit.text
        second = await wait_status(client, second_submit.json()["generation"]["id"], {"succeeded"})
        assert second["version_no"] == 2
        assert second["reference_asset_id"] is None
        assert second["brief_snapshot"]["reference_asset_id"] is None
        assert len(visual.submit_calls) == 2
        assert visual.submit_calls[1].reference_path is None
        assert visual.submit_calls[1].reference_mime_type is None

        project_detail = await detail(client, project_id)
        assert project_detail["project"]["reference_asset"] is None
        assert project_detail["generations"][0]["reference_asset_id"] == reference_asset["id"]
        assert project_detail["generations"][0]["result_asset_id"] == first["result_asset_id"]
        assert project_detail["generations"][1]["reference_asset_id"] is None


@pytest.mark.asyncio
async def test_failure_does_not_reuse_version_and_old_success_stays_downloadable(full_settings):
    first_image = png_bytes(color=(10, 180, 10))
    third_image = png_bytes(color=(180, 10, 10))
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=first_image))
    async with environment(full_settings, visual) as (client, _llm, visual):
        confirmed = await create_confirmed_without_reference(client)
        first_submit = await submit(client, confirmed)
        first = await wait_status(client, first_submit.json()["generation"]["id"], {"succeeded"})

        visual.submit_outcome = ProviderOutcome(
            state="failed",
            evidence="rejected",
            error_code="VISUAL_REJECTED",
            error_message="供应商拒绝生成",
        )
        second_submit = await submit(client, confirmed)
        second = await wait_status(client, second_submit.json()["generation"]["id"], {"failed"})
        assert second["version_no"] == 2
        assert second["result_asset_id"] is None
        assert second["result_asset"] is None
        assert second["error"]["code"] == "VISUAL_REJECTED"

        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=third_image)
        third_submit = await submit(client, confirmed)
        third = await wait_status(client, third_submit.json()["generation"]["id"], {"succeeded"})
        assert third["version_no"] == 3
        assert third["result_asset_id"] != first["result_asset_id"]

        project_detail = await detail(client, confirmed["id"])
        versions = [item["version_no"] for item in project_detail["generations"]]
        assert versions == [1, 2, 3]
        statuses = [item["status"] for item in project_detail["generations"]]
        assert statuses == ["succeeded", "failed", "succeeded"]
        assert project_detail["generations"][0]["result_asset_id"] == first["result_asset_id"]
        assert project_detail["generations"][1]["result_asset_id"] is None
        assert project_detail["generations"][2]["result_asset_id"] == third["result_asset_id"]
        assert len({item["id"] for item in project_detail["generations"]}) == 3
        old_download = await client.get(f"/api/assets/{first['result_asset_id']}?download=true")
        assert old_download.status_code == 200
        assert old_download.content == first_image
        assert len(visual.submit_calls) == 3


@pytest.mark.asyncio
async def test_missing_reference_file_fails_background_without_submit(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        gate=asyncio.Event(),
    )
    async with environment(full_settings, visual) as (client, _llm, visual):
        confirmed, reference_asset = await create_confirmed_with_reference(
            client, png_bytes(color=(10, 10, 200))
        )
        response = await submit(client, confirmed)
        assert response.status_code == 202, response.text
        generation_id = response.json()["generation"]["id"]

        with sqlite3.connect(full_settings.database_path) as connection:
            row = connection.execute(
                "SELECT relative_path FROM assets WHERE id = ?", (reference_asset["id"],)
            ).fetchone()
        assert row is not None
        (full_settings.resolved_data_dir() / row[0]).unlink()
        visual.gate.set()
        failed = await wait_status(client, generation_id, {"failed"})
        assert failed["error"]["code"] == "REFERENCE_FILE_MISSING"
        assert visual.submit_calls == []
        assert failed["result_asset_id"] is None


@pytest.mark.asyncio
async def test_cross_project_reference_is_rejected_before_adapter_call(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual) as (client, _llm, visual):
        first = await create_confirmed_without_reference(client)
        second = await create_project(client)
        second_uploaded = await upload_ok(
            client, second["id"], second["revision"], png_bytes(color=(200, 200, 10))
        )
        other_asset_id = second_uploaded["reference_asset"]["id"]

        with sqlite3.connect(full_settings.database_path) as connection:
            connection.execute(
                "UPDATE projects SET reference_asset_id = ? WHERE id = ?",
                (other_asset_id, first["id"]),
            )
            connection.commit()

        response = await submit(client, first)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "REFERENCE_NOT_SUPPORTED"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_unsupported_reference_capability_blocks_then_removal_allows_text_generation(
    full_settings,
):
    visual = VisualStub(
        capabilities=replace(VisualStub().capabilities, reference_image=False),
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
    )
    async with environment(full_settings, visual) as (client, _llm, visual):
        confirmed, _reference_asset = await create_confirmed_with_reference(
            client, png_bytes(color=(100, 100, 100))
        )
        blocked = await submit(client, confirmed)
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["code"] == "REFERENCE_NOT_SUPPORTED"
        assert visual.submit_calls == []

        removed = await client.delete(
            f"/api/projects/{confirmed['id']}/reference?expected_revision={confirmed['revision']}"
        )
        assert removed.status_code == 200, removed.text
        confirmed_two = await organize_and_confirm(
            client, removed.json(), "移除不支持参考图后按纯文字需求重新整理"
        )
        success = await submit(client, confirmed_two)
        assert success.status_code == 202, success.text
        result = await wait_status(client, success.json()["generation"]["id"], {"succeeded"})
        assert result["version_no"] == 1
        assert result["reference_asset_id"] is None
        assert len(visual.submit_calls) == 1
        assert visual.submit_calls[0].reference_path is None
