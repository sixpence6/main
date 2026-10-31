from __future__ import annotations

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import write_transaction
from app.main import create_app
from app.models import Project
from tests.conftest import migrate
from tests.support.auth import authenticated_client


async def create_project(client: AsyncClient) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_project_returns_initial_project_view(client):
    project = await create_project(client)
    assert project["revision"] == 0
    assert project["title"] == "未命名项目"
    assert project["brief_status"] == "draft"
    assert project["prompt_revision"] is None
    assert project["confirmed_revision"] is None
    assert project["brief"] == {
        "product_description": "",
        "purpose": "",
        "style": "",
        "composition": "",
        "text_content": "",
        "image_size": None,
        "reference_asset_id": None,
        "prompt": "",
    }
    assert project["reference_asset"] is None
    assert project["brief_error"] is None
    assert project["created_at"] == project["updated_at"]
    assert project["created_at"].endswith("Z")
    int(project["id"].replace("-", ""), 16)


async def test_create_project_rejects_unknown_payload_fields(client):
    response = await client.post("/api/projects", json={"title": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_project_detail_returns_empty_collections(client):
    project = await create_project(client)
    response = await client.get(f"/api/projects/{project['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["project"]["id"] == project["id"]
    assert body["messages"] == []
    assert body["generations"] == []


async def test_missing_project_returns_safe_404(client):
    response = await client.get("/api/projects/not-a-project")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}}


async def test_list_projects_empty(client):
    response = await client.get("/api/projects")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_pagination_orders_by_updated_at_then_id(app, client):
    created = [await create_project(client) for _ in range(3)]
    # Force identical timestamps so the tie-breaker is exercised deterministically.
    async with write_transaction(app.state.session_factory) as session:
        for project in created:
            row = await session.get(Project, project["id"])
            assert row is not None
            row.updated_at = "2026-01-01T00:00:00.000Z"
    expected_order = sorted((project["id"] for project in created), reverse=True)

    first = await client.get("/api/projects", params={"limit": 2})
    assert first.status_code == 200
    first_body = first.json()
    assert [item["id"] for item in first_body["items"]] == expected_order[:2]
    assert first_body["next_cursor"]
    assert all(item["latest_generation_status"] is None for item in first_body["items"])

    second = await client.get(
        "/api/projects", params={"limit": 2, "cursor": first_body["next_cursor"]}
    )
    assert second.status_code == 200
    second_body = second.json()
    assert [item["id"] for item in second_body["items"]] == expected_order[2:]
    assert second_body["next_cursor"] is None


async def test_pagination_cursor_roundtrip_is_stable_and_exact(client):
    created = [await create_project(client) for _ in range(3)]
    page = await client.get("/api/projects", params={"limit": 2})
    cursor = page.json()["next_cursor"]
    again = await client.get("/api/projects", params={"limit": 2, "cursor": cursor})
    again_again = await client.get("/api/projects", params={"limit": 2, "cursor": cursor})
    assert [item["id"] for item in again.json()["items"]] == [
        item["id"] for item in again_again.json()["items"]
    ]
    assert len(again.json()["items"]) == len(created) - 2


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!!",
        base64.urlsafe_b64encode(b"[]").decode().rstrip("="),
        base64.urlsafe_b64encode(json.dumps({"id": "x", "updated_at": "y"}).encode())
        .decode()
        .rstrip("="),
    ],
)
async def test_invalid_cursor_returns_422(client, cursor):
    response = await client.get("/api/projects", params={"cursor": cursor})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CURSOR"


@pytest.mark.parametrize("limit", [0, 101, "abc"])
async def test_limit_is_validated(client, limit):
    response = await client.get("/api/projects", params={"limit": limit})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_reads_do_not_touch_project_state(app, client):
    project = await create_project(client)
    before = project
    await client.get("/api/projects")
    await client.get(f"/api/projects/{project['id']}")
    after = (await client.get(f"/api/projects/{project['id']}")).json()["project"]
    assert after == before


async def test_project_survives_process_restart(settings, registry):
    migrate(settings)
    first_app = create_app(settings=settings, registry=registry)
    async with first_app.router.lifespan_context(first_app):
        async with authenticated_client(first_app) as first_client:
            project = await create_project(first_client)

    second_app = create_app(settings=settings, registry=registry)
    async with second_app.router.lifespan_context(second_app):
        async with authenticated_client(second_app) as second_client:
            response = await second_client.get(f"/api/projects/{project['id']}")
            assert response.status_code == 200
            assert response.json()["project"]["id"] == project["id"]
            listing = await second_client.get("/api/projects")
            assert [item["id"] for item in listing.json()["items"]] == [project["id"]]


async def test_openapi_documents_project_endpoints(client):
    document = (await client.get("/api/openapi.json")).json()
    paths = document["paths"]
    assert "post" in paths["/api/projects"]
    assert "get" in paths["/api/projects"]
    assert "get" in paths["/api/projects/{project_id}"]
