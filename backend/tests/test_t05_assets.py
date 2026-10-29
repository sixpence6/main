from __future__ import annotations

import io
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models import Asset
from app.timeutil import format_timestamp, utc_now
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate


def png_bytes(width: int = 16, height: int = 12, color=(220, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def jpeg_bytes(width: int = 20, height: int = 14, color=(30, 80, 220)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "JPEG")
    return buffer.getvalue()


def noise_png(width: int = 200, height: int = 200) -> bytes:
    buffer = io.BytesIO()
    Image.frombytes("RGB", (width, height), os.urandom(width * height * 3)).save(buffer, "PNG")
    return buffer.getvalue()


def gif_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("P", (8, 8)).save(buffer, "GIF")
    return buffer.getvalue()


@asynccontextmanager
async def environment(settings: Settings, visual: VisualStub | None = None):
    llm = LLMStub()
    visual = visual or VisualStub()
    registry = build_test_registry(llm=llm, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, registry, llm, visual, application


async def create_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201
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


async def upload_ok(client, project_id: str, revision: int, data: bytes, **kwargs) -> dict:
    response = await upload(client, project_id, revision, data, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_upload_png_bumps_revision_and_serves_metadata_and_bytes(full_settings):
    async with environment(full_settings) as (client, registry, llm, _visual, _app):
        project = await create_project(client)
        data = png_bytes()
        uploaded = await upload_ok(client, project["id"], 0, data)
        assert uploaded["revision"] == 1
        assert uploaded["brief_status"] == "draft"
        assert uploaded["prompt_revision"] is None
        assert uploaded["confirmed_revision"] is None
        asset = uploaded["reference_asset"]
        assert asset is not None
        assert asset["kind"] == "reference"
        assert asset["mime_type"] == "image/png"
        assert (asset["width"], asset["height"]) == (16, 12)
        assert asset["byte_size"] == len(data)
        assert asset["availability"] == "available"

        preview = await client.get(f"/api/assets/{asset['id']}")
        assert preview.status_code == 200
        assert preview.content == data
        assert preview.headers["content-type"] == "image/png"
        assert "attachment" not in preview.headers.get("content-disposition", "")

        download = await client.get(f"/api/assets/{asset['id']}?download=true")
        assert download.status_code == 200
        disposition = download.headers["content-disposition"]
        assert disposition.startswith("attachment")
        assert asset["id"] in disposition
        assert ".png" in disposition

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["reference_asset"]["id"] == asset["id"]
        assert detail["project"]["reference_asset"]["availability"] == "available"
        assert llm.calls == []
        assert registry.recorder.visual_builds == 0


@pytest.mark.asyncio
async def test_upload_uses_actual_mime_not_filename_or_part_type(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        data = jpeg_bytes()
        uploaded = await upload_ok(
            client,
            project["id"],
            0,
            data,
            filename="pretend.png",
            content_type="image/png",
        )
        asset = uploaded["reference_asset"]
        assert asset["mime_type"] == "image/jpeg"
        download = await client.get(f"/api/assets/{asset['id']}?download=true")
        assert ".jpg" in download.headers["content-disposition"]
        preview = await client.get(f"/api/assets/{asset['id']}")
        assert preview.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_replace_keeps_old_asset_readable(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        first_data = png_bytes(color=(200, 10, 10))
        first = await upload_ok(client, project["id"], 0, first_data)
        first_id = first["reference_asset"]["id"]

        second_data = png_bytes(color=(10, 10, 200))
        second = await upload_ok(client, project["id"], 1, second_data)
        second_id = second["reference_asset"]["id"]
        assert second["revision"] == 2
        assert second_id != first_id

        old = await client.get(f"/api/assets/{first_id}")
        assert old.status_code == 200
        assert old.content == first_data
        new = await client.get(f"/api/assets/{second_id}")
        assert new.content == second_data
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["reference_asset"]["id"] == second_id


@pytest.mark.asyncio
async def test_remove_reference_clears_slot_and_is_noop_when_empty(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        empty = await client.delete(f"/api/projects/{project['id']}/reference?expected_revision=0")
        assert empty.status_code == 200
        assert empty.json()["revision"] == 0
        assert empty.json()["reference_asset"] is None

        data = png_bytes()
        uploaded = await upload_ok(client, project["id"], 0, data)
        asset_id = uploaded["reference_asset"]["id"]
        removed = await client.delete(
            f"/api/projects/{project['id']}/reference?expected_revision={uploaded['revision']}"
        )
        assert removed.status_code == 200
        removed_project = removed.json()
        assert removed_project["reference_asset"] is None
        assert removed_project["revision"] == uploaded["revision"] + 1
        assert removed_project["brief_status"] == "draft"
        assert (await client.get(f"/api/assets/{asset_id}")).status_code == 200

        again = await client.delete(
            f"/api/projects/{project['id']}/reference?expected_revision={removed_project['revision']}"
        )
        assert again.status_code == 200
        assert again.json()["revision"] == removed_project["revision"]
        assert again.json()["updated_at"] == removed_project["updated_at"]


@pytest.mark.asyncio
async def test_stale_revision_does_not_replace_reference(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        first = await upload_ok(client, project["id"], 0, png_bytes())
        first_id = first["reference_asset"]["id"]
        response = await upload(client, project["id"], 0, png_bytes(color=(1, 2, 3)))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "REVISION_CONFLICT"
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["reference_asset"]["id"] == first_id
        files = list((full_settings.resolved_data_dir() / "assets").glob("*"))
        assert len(files) == 1


@pytest.mark.asyncio
async def test_failed_upload_keeps_old_reference(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        first = await upload_ok(client, project["id"], 0, png_bytes())
        first_id = first["reference_asset"]["id"]
        response = await upload(client, project["id"], 1, b"not an image")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REFERENCE_IMAGE"
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["reference_asset"]["id"] == first_id
        assert detail["project"]["revision"] == 1


@pytest.mark.asyncio
async def test_upload_rejects_invalid_multipart_payloads(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        url = f"/api/projects/{project['id']}/reference"
        data = png_bytes()
        cases = []

        cases.append(
            await client.post(
                url,
                files=[
                    ("file", ("a.png", data, "image/png")),
                    ("file", ("b.png", data, "image/png")),
                ],
                data={"expected_revision": "0"},
            )
        )
        cases.append(
            await client.post(
                url,
                files={"file": ("a.png", data, "image/png")},
                data={"expected_revision": "0", "extra": "x"},
            )
        )
        boundary = "duplicatefields"
        duplicate_body = (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"expected_revision\"\r\n\r\n0\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"expected_revision\"\r\n\r\n1\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"file\"; filename=\"a.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        cases.append(
            await client.post(
                url,
                content=duplicate_body,
                headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            )
        )
        cases.append(await client.post(url, data={"expected_revision": "0"}))
        cases.append(
            await client.post(
                url,
                files={"file": ("a.png", data, "image/png")},
                data={"expected_revision": "abc"},
            )
        )
        cases.append(
            await client.post(
                url,
                files={"file": ("a.gif", gif_bytes(), "image/gif")},
                data={"expected_revision": "0"},
            )
        )
        cases.append(
            await client.post(
                url,
                files={"file": ("a.png", b"broken", "image/png")},
                data={"expected_revision": "0"},
            )
        )
        cases.append(await client.post(url, content=b"not multipart"))

        for response in cases:
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] in {
                "VALIDATION_ERROR",
                "UNSUPPORTED_REFERENCE_FORMAT",
                "INVALID_REFERENCE_IMAGE",
            }
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["revision"] == 0
        assert detail["project"]["reference_asset"] is None


@pytest.mark.asyncio
async def test_upload_respects_provider_cap(full_settings):
    capabilities = replace(VisualStub().capabilities, provider_max_reference_bytes=1024)
    visual = VisualStub(capabilities=capabilities)
    async with environment(full_settings, visual=visual) as (
        client,
        _registry,
        _llm,
        _visual,
        _app,
    ):
        project = await create_project(client)
        large = noise_png()
        assert len(large) > 1024
        response = await upload(client, project["id"], 0, large)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        small = png_bytes(width=4, height=4)
        assert len(small) < 1024
        uploaded = await upload_ok(client, project["id"], 0, small)
        assert uploaded["reference_asset"]["byte_size"] == len(small)


@pytest.mark.asyncio
async def test_upload_hard_cap_boundary_and_unlimited_length_stream(full_settings):
    capabilities = replace(VisualStub().capabilities, provider_max_reference_bytes=None)
    visual = VisualStub(capabilities=capabilities)
    async with environment(full_settings, visual=visual) as (
        client,
        _registry,
        _llm,
        _visual,
        _app,
    ):
        project = await create_project(client)
        exactly = b"x" * 10_000_000
        response = await upload(client, project["id"], 0, exactly)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REFERENCE_IMAGE"

        too_large = b"x" * 10_000_001
        response = await upload(client, project["id"], 0, too_large)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"

        boundary = "t05boundary"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"expected_revision\"\r\n\r\n0\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"file\"; filename=\"a.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + too_large + f"\r\n--{boundary}--\r\n".encode()

        async def chunks():
            for offset in range(0, len(body), 8192):
                yield body[offset : offset + 8192]

        streamed = await client.post(
            f"/api/projects/{project['id']}/reference",
            content=chunks(),
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        assert streamed.status_code == 413
        assert streamed.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_missing_asset_file_keeps_metadata_and_reports_missing(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        project = await create_project(client)
        data = png_bytes()
        uploaded = await upload_ok(client, project["id"], 0, data)
        asset_id = uploaded["reference_asset"]["id"]
        path = full_settings.resolved_data_dir() / "assets" / f"{asset_id}.png"
        assert path.is_file()
        path.unlink()

        response = await client.get(f"/api/assets/{asset_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ASSET_FILE_MISSING"
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        reference = detail["project"]["reference_asset"]
        assert reference["id"] == asset_id
        assert reference["availability"] == "missing"


@pytest.mark.asyncio
async def test_asset_path_traversal_is_not_served(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, application):
        project = await create_project(client)
        outside = full_settings.resolved_data_dir().parent / "outside.png"
        outside.write_bytes(png_bytes())
        asset_id = str(uuid4())
        now = format_timestamp(utc_now())
        async with application.state.session_factory() as session:
            async with session.begin():
                session.add(
                    Asset(
                        id=asset_id,
                        project_id=project["id"],
                        kind="reference",
                        relative_path="../outside.png",
                        mime_type="image/png",
                        width=16,
                        height=12,
                        byte_size=len(png_bytes()),
                        created_at=now,
                    )
                )
        response = await client.get(f"/api/assets/{asset_id}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ASSET_FILE_MISSING"


@pytest.mark.asyncio
async def test_unknown_asset_and_stale_delete(full_settings):
    async with environment(full_settings) as (client, _registry, _llm, _visual, _app):
        missing = await client.get(f"/api/assets/{uuid4()}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "ASSET_NOT_FOUND"

        project = await create_project(client)
        uploaded = await upload_ok(client, project["id"], 0, png_bytes())
        stale = await client.delete(
            f"/api/projects/{project['id']}/reference?expected_revision={uploaded['revision'] - 1}"
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["project"]["reference_asset"]["id"] == uploaded["reference_asset"]["id"]
