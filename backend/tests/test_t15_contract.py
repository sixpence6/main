from __future__ import annotations

from typing import Any

EXPECTED_OPERATIONS = 20
EXPECTED_AUTH_OPERATIONS = {
    ("GET", "/api/auth/csrf"),
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
}


def _operations(spec: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, node in spec["paths"].items()
        for method in node
        if method in {"get", "post", "patch", "delete", "put"}
    }


def _json_schema(spec: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    return response["content"]["application/json"]["schema"]


def _ref_name(schema: dict[str, Any]) -> str:
    return schema["$ref"].rsplit("/", 1)[-1]


async def test_openapi_documents_every_public_operation(client):
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    operations = _operations(spec)
    assert len(operations) == EXPECTED_OPERATIONS
    assert ("GET", "/api/health") in operations
    assert EXPECTED_AUTH_OPERATIONS <= operations
    assert ("GET", "/api/assets/{asset_id}") in operations
    operation_ids = [
        node[method]["operationId"]
        for node in spec["paths"].values()
        for method in node
        if method in {"get", "post", "patch", "delete", "put"}
    ]
    assert len(operation_ids) == len(set(operation_ids)) == EXPECTED_OPERATIONS


async def test_error_responses_use_the_public_envelope(client):
    spec = (await client.get("/api/openapi.json")).json()
    assert "ErrorResponse" in spec["components"]["schemas"]
    error_schema = spec["components"]["schemas"]["ErrorResponse"]
    assert error_schema["required"] == ["error"]
    assert error_schema["properties"]["error"]["$ref"].endswith("/ErrorView")

    checked = 0
    for path, node in spec["paths"].items():
        for method, operation in node.items():
            if method not in {"get", "post", "patch", "delete", "put"}:
                continue
            for status, response in operation["responses"].items():
                if not status.startswith(("4", "5")):
                    continue
                assert "application/json" in response["content"], (method, path, status)
                assert _ref_name(_json_schema(spec, response)) == "ErrorResponse", (
                    method,
                    path,
                    status,
                )
                checked += 1
    assert checked >= 25


async def test_generation_contract_documents_create_and_replay(client):
    spec = (await client.get("/api/openapi.json")).json()
    operation = spec["paths"]["/api/projects/{project_id}/generations"]["post"]
    assert operation["responses"]["202"]["description"] == "Successful Response"
    assert operation["responses"]["200"]["description"].startswith("幂等重放")
    for status in ("200", "202"):
        assert _ref_name(_json_schema(spec, operation["responses"][status])) == "GenerationResponse"


async def test_asset_contract_documents_image_media_types(client):
    spec = (await client.get("/api/openapi.json")).json()
    operation = spec["paths"]["/api/assets/{asset_id}"]["get"]
    media = operation["responses"]["200"]["content"]
    assert set(media) == {"image/png", "image/jpeg"}


async def test_openapi_exposes_public_dtos_without_internal_fields(client):
    spec = (await client.get("/api/openapi.json")).json()
    components = spec["components"]["schemas"]
    for name in (
        "ProjectView",
        "ProjectDetailResponse",
        "ProjectListResponse",
        "MessageView",
        "GenerationView",
        "GenerationResponse",
        "ConfigStatus",
        "ErrorResponse",
    ):
        assert name in components, name
    generation = components["GenerationView"]["properties"]
    assert "request_id" in generation
    assert "can_retrieve_result" in generation
    assert "reference_asset_id" in generation
    project = components["ProjectView"]["properties"]
    assert {"revision", "prompt_revision", "confirmed_revision", "brief_status"} <= set(project)
