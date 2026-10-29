from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.adapters.base import BriefData, OrganizeResult
from app.config import Settings
from app.db import build_session_factory, create_engine, write_transaction
from app.models import Message, Project
from tests.support.adapters import build_test_registry
from tests.support.apps import llm_settings as build_llm_settings
from tests.support.apps import run_client

REVIEW_BRIEF = BriefData(
    product_description="无线蓝牙耳机",
    purpose="电商主图",
    style="干净明亮",
    composition="居中构图",
    text_content="限时优惠",
    prompt="白色背景中居中展示无线蓝牙耳机，干净明亮，柔和阴影",
)


def responder(outcome: str, brief: BriefData, message: str = "已整理"):
    def _respond(_request):
        return OrganizeResult(outcome=outcome, assistant_message=message, brief=brief)

    return _respond


async def create_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201
    return response.json()


async def send(client, project_id: str, content: str, revision: int = 0):
    return await client.post(
        f"/api/projects/{project_id}/messages",
        json={"content": content, "expected_revision": revision},
    )


async def get_project(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_unconfigured_llm_saves_nothing(client, llm_app):
    project = await create_project(client)
    response = await send(client, project["id"], "你好")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
    detail = await get_project(client, project["id"])
    assert detail["messages"] == []
    assert detail["project"]["revision"] == 0
    assert detail["project"]["brief_status"] == "draft"
    assert llm_app.state.registry.stub_llm.calls == []


@pytest.mark.asyncio
async def test_unknown_adapter_saves_nothing(settings: Settings):
    registry = build_test_registry()
    misconfigured = build_llm_settings(settings, llm_adapter="missing-llm")
    async with run_client(misconfigured, registry) as client:
        project = await create_project(client)
        response = await send(client, project["id"], "你好")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ADAPTER_UNAVAILABLE"
        detail = await get_project(client, project["id"])
        assert detail["messages"] == []
        assert detail["project"]["revision"] == 0
        assert registry.stub_llm.calls == []


@pytest.mark.asyncio
async def test_needs_input_keeps_user_text_and_snapshot(llm_client, llm_app):
    llm_app.state.registry.stub_llm.responder = responder(
        "needs_input", BriefData(product_description="无线蓝牙耳机"), "请补充目标人群"
    )
    project = await create_project(llm_client)
    response = await send(llm_client, project["id"], "  帮我   做一张耳机主图  ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["revision"] == 2
    assert body["project"]["brief_status"] == "needs_input"
    assert body["project"]["prompt_revision"] is None
    assert body["project"]["confirmed_revision"] is None
    assert body["project"]["title"] == "帮我 做一张耳机主图"
    assert body["project"]["brief"]["product_description"] == "无线蓝牙耳机"
    assert body["user_message"]["sequence_no"] == 1
    assert body["user_message"]["content"] == "  帮我   做一张耳机主图  "
    assert body["user_message"]["brief_snapshot"] is None
    assert body["assistant_message"]["sequence_no"] == 2
    assert body["assistant_message"]["brief_snapshot"]["product_description"] == "无线蓝牙耳机"

    detail = await get_project(llm_client, project["id"])
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    request = llm_app.state.registry.stub_llm.calls[0]
    assert request.user_text.strip() == "帮我   做一张耳机主图"


@pytest.mark.asyncio
async def test_review_sets_prompt_revision(llm_client, llm_app):
    llm_app.state.registry.stub_llm.responder = responder("review", REVIEW_BRIEF, "需求卡已就绪")
    project = await create_project(llm_client)
    response = await send(llm_client, project["id"], "耳机主图，干净明亮", 0)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project"]["revision"] == 2
    assert body["project"]["prompt_revision"] == 2
    assert body["project"]["brief_status"] == "review"
    assert body["project"]["brief"]["prompt"] == REVIEW_BRIEF.prompt
    assert body["project"]["brief"]["image_size"] is None
    assert body["project"]["brief"]["reference_asset_id"] is None
    assert body["assistant_message"]["brief_snapshot"]["prompt"] == REVIEW_BRIEF.prompt


@pytest.mark.asyncio
async def test_invalid_review_restores_draft_and_keeps_user_message(llm_client, llm_app):
    llm_app.state.registry.stub_llm.responder = responder(
        "review", replace(REVIEW_BRIEF, prompt="   "), "缺少提示词"
    )
    project = await create_project(llm_client)
    response = await send(llm_client, project["id"], "耳机主图", 0)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_RESPONSE_INVALID"
    detail = await get_project(llm_client, project["id"])
    assert detail["project"]["revision"] == 1
    assert detail["project"]["brief_status"] == "draft"
    assert detail["project"]["brief_error"]["code"] == "ORGANIZATION_FAILED"
    assert [message["role"] for message in detail["messages"]] == ["user"]


@pytest.mark.asyncio
async def test_adapter_exception_returns_502_without_state_loss(llm_client, llm_app):
    def explode(_request):
        raise RuntimeError("provider down")

    llm_app.state.registry.stub_llm.responder = explode
    project = await create_project(llm_client)
    response = await send(llm_client, project["id"], "耳机主图", 0)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "LLM_CALL_FAILED"
    detail = await get_project(llm_client, project["id"])
    assert detail["project"]["revision"] == 1
    assert detail["project"]["brief_status"] == "draft"
    assert detail["project"]["brief_error"]["code"] == "ORGANIZATION_FAILED"
    assert len(detail["messages"]) == 1


@pytest.mark.asyncio
async def test_timeout_returns_504(settings: Settings):
    registry = build_test_registry()
    registry.stub_llm.delay_seconds = 1.5
    tight = build_llm_settings(settings, llm_timeout_seconds=1)
    async with run_client(tight, registry) as client:
        project = await create_project(client)
        response = await send(client, project["id"], "耳机主图", 0)
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "LLM_TIMEOUT"
        detail = await get_project(client, project["id"])
        assert detail["project"]["revision"] == 1
        assert detail["project"]["brief_status"] == "draft"
        assert detail["project"]["brief_error"]["code"] == "ORGANIZATION_TIMEOUT"
        assert len(detail["messages"]) == 1


@pytest.mark.asyncio
async def test_revision_conflict_does_not_call_model(llm_client, llm_app):
    project = await create_project(llm_client)
    response = await send(llm_client, project["id"], "耳机主图", 3)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVISION_CONFLICT"
    assert llm_app.state.registry.stub_llm.calls == []
    detail = await get_project(llm_client, project["id"])
    assert detail["messages"] == []


@pytest.mark.asyncio
async def test_blank_and_oversized_messages_rejected(llm_client, llm_app):
    project = await create_project(llm_client)
    blank = await send(llm_client, project["id"], "   ", 0)
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"
    oversized = await send(llm_client, project["id"], "x" * 20_001, 0)
    assert oversized.status_code == 422
    assert oversized.json()["error"]["code"] == "VALIDATION_ERROR"
    assert llm_app.state.registry.stub_llm.calls == []


@pytest.mark.asyncio
async def test_concurrent_organize_rejected_with_project_busy(llm_client, llm_app):
    stub = llm_app.state.registry.stub_llm
    stub.responder = responder("needs_input", BriefData(), "请补充信息")
    stub.gate = asyncio.Event()
    project = await create_project(llm_client)
    first = asyncio.create_task(send(llm_client, project["id"], "第一段", 0))
    await wait_for_calls(stub)
    second = await send(llm_client, project["id"], "第二段", 1)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PROJECT_BUSY"
    stub.gate.set()
    assert (await first).status_code == 200
    detail = await get_project(llm_client, project["id"])
    assert [message["content"] for message in detail["messages"]] == ["第一段", "请补充信息"]
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_detail_readable_while_organizing(llm_client, llm_app):
    stub = llm_app.state.registry.stub_llm
    stub.responder = responder("needs_input", BriefData(), "请补充信息")
    stub.gate = asyncio.Event()
    project = await create_project(llm_client)
    task = asyncio.create_task(send(llm_client, project["id"], "先行消息", 0))
    await wait_for_calls(stub)
    detail = await get_project(llm_client, project["id"])
    assert detail["project"]["brief_status"] == "organizing"
    assert detail["project"]["revision"] == 1
    assert detail["messages"][0]["content"] == "先行消息"
    stub.gate.set()
    assert (await task).status_code == 200


@pytest.mark.asyncio
async def test_late_result_cannot_overwrite_newer_state(settings: Settings, llm_settings: Settings):
    registry = build_test_registry()
    registry.stub_llm.responder = responder("review", REVIEW_BRIEF, "需求卡已就绪")
    registry.stub_llm.gate = asyncio.Event()
    async with run_client(llm_settings, registry) as client:
        project = await create_project(client)
        task = asyncio.create_task(send(client, project["id"], "耳机主图", 0))
        await wait_for_calls(registry.stub_llm)
        engine = create_engine(llm_settings)
        session_factory = build_session_factory(engine)
        async with write_transaction(session_factory) as session:
            row = await session.get(Project, project["id"])
            row.revision = 5
            row.brief_status = "draft"
            row.organize_operation_id = None
        await engine.dispose()
        registry.stub_llm.gate.set()
        response = await task
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "STALE_ORGANIZATION"
        detail = await get_project(client, project["id"])
        assert detail["project"]["revision"] == 5
        assert [message["role"] for message in detail["messages"]] == ["user"]


@pytest.mark.asyncio
async def test_restart_repairs_organizing_once(settings: Settings):
    registry = build_test_registry()
    config = build_llm_settings(settings)
    async with run_client(config, registry) as client:
        project = await create_project(client)
    engine = create_engine(config)
    session_factory = build_session_factory(engine)
    async with write_transaction(session_factory) as session:
        row = await session.get(Project, project["id"])
        row.brief_status = "organizing"
        row.revision = 1
        row.organize_operation_id = "11111111-1111-4111-8111-111111111111"
        row.updated_at = "2026-01-01T00:00:00.000Z"
        session.add(
            Message(
                id="22222222-2222-4222-8222-222222222222",
                project_id=project["id"],
                sequence_no=1,
                role="user",
                content="被中断的消息",
                project_revision=1,
                brief_snapshot_json=None,
                operation_id="11111111-1111-4111-8111-111111111111",
                created_at="2026-01-01T00:00:00.000Z",
            )
        )
    await engine.dispose()

    for _ in range(2):
        async with run_client(config, registry) as client:
            detail = await get_project(client, project["id"])
            assert detail["project"]["brief_status"] == "draft"
            assert detail["project"]["brief_error"]["code"] == "ORGANIZATION_INTERRUPTED"
            assert detail["project"]["prompt_revision"] is None
            assert detail["project"]["confirmed_revision"] is None
            assert [message["content"] for message in detail["messages"]] == ["被中断的消息"]
            assert len(detail["messages"]) == 1
    assert registry.stub_llm.calls == []


async def wait_for_calls(stub) -> None:
    for _ in range(200):
        if stub.calls:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("adapter was never called")
