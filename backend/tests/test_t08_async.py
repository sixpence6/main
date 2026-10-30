from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from PIL import Image

from app.adapters.base import (
    BriefData,
    OrganizeRequest,
    OrganizeResult,
    ProviderError,
    ProviderOutcome,
)
from app.config import Settings
from app.services import generations as generation_service
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
            "SELECT remote_task_id, result_locator_json, error_code, error_message, "
            "status, stage, result_asset_id FROM generations WHERE id = ?",
            (generation_id,),
        ).fetchone()


async def submit(client, project: dict) -> dict:
    response = await client.post(
        f"/api/projects/{project['id']}/generations", json=generation_payload(project)
    )
    assert response.status_code == 202, response.text
    return response.json()["generation"]


@pytest.mark.asyncio
async def test_async_submission_polls_original_task_until_result(full_settings):
    image = png_bytes()
    gate = asyncio.Event()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="task-1"),
        poll_outcomes=[
            ProviderOutcome(state="succeeded", remote_task_id="task-1", image_bytes=image)
        ],
        poll_gate=gate,
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)

        for _ in range(200):
            if visual.poll_calls:
                break
            await asyncio.sleep(0.01)
        assert visual.poll_calls == ["task-1"], visual.poll_calls
        waiting = (await client.get(f"/api/generations/{created['id']}")).json()
        assert waiting["status"] == "running"
        assert waiting["stage"] == "waiting_remote"
        assert waiting["remote_task_id"] == "task-1"
        assert waiting["result_asset_id"] is None

        gate.set()
        done = await wait_status(client, created["id"], {"succeeded"})
        assert done["stage"] == "done"
        assert done["remote_task_id"] == "task-1"
        assert done["result_asset"]["mime_type"] == "image/png"
        assert done["result_asset"]["width"] == 1024
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == ["task-1"]
        assert visual.fetch_calls == []
        row = generation_row(full_settings, created["id"])
        assert row[0] == "task-1"
        assert row[4] == "succeeded"


@pytest.mark.asyncio
async def test_transient_poll_exception_retries_without_resubmitting(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="task-2"),
        poll_errors=[RuntimeError("temporary read failure")],
        poll_outcomes=[
            ProviderOutcome(state="succeeded", remote_task_id="task-2", image_bytes=png_bytes())
        ],
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        done = await wait_status(client, created["id"], {"succeeded"})
        assert done["result_asset_id"]
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == ["task-2", "task-2"]


@pytest.mark.asyncio
async def test_transient_poll_provider_error_retries_without_resubmitting(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="task-2b"),
        poll_errors=[ProviderError("临时状态不可读", evidence="uncertain")],
        poll_outcomes=[
            ProviderOutcome(state="succeeded", remote_task_id="task-2b", image_bytes=png_bytes())
        ],
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        await wait_status(client, created["id"], {"succeeded"})
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == ["task-2b", "task-2b"]


@pytest.mark.asyncio
async def test_total_wait_poll_exhaustion_is_unknown_with_remote_identity(full_settings):
    # 整套测试并行加载时，1 秒预算可能在首次 poll 前就被事件循环调度耗尽；
    # 这里保留“总等待耗尽 -> unknown”的语义，同时给首次 poll 留出确定窗口。
    settings = full_settings.model_copy(update={"visual_timeout_seconds": 3})
    locator = "https://cdn.example/late.png?signature=private"
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="task-timeout"),
        poll_outcomes=[
            ProviderOutcome(
                state="pending",
                result_locator=locator,
                result_expires_at="2099-01-01T00:00:00Z",
            )
        ],
        poll_delay_seconds=0.2,
    )
    async with environment(settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"unknown"}, timeout_seconds=8)
        assert finished["stage"] == "waiting_remote"
        assert finished["remote_task_id"] == "task-timeout"
        assert finished["error"]["code"] == "VISUAL_OUTCOME_UNKNOWN"
        assert finished["result_asset_id"] is None
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls
        row = generation_row(settings, created["id"])
        assert row[0] == "task-timeout"
        assert row[4] == "unknown"
        assert json.loads(row[1]) == {
            "locator": locator,
            "expires_at": "2099-01-01T00:00:00Z",
        }


@pytest.mark.asyncio
async def test_total_wait_covers_slow_submit(full_settings):
    settings = full_settings.model_copy(update={"visual_timeout_seconds": 1})
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()),
        delay_seconds=2.0,
    )
    async with environment(settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"unknown"}, timeout_seconds=4)
        assert finished["stage"] == "submitting"
        assert finished["error"]["code"] == "VISUAL_OUTCOME_UNKNOWN"
        assert finished["result_asset_id"] is None
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == []


@pytest.mark.asyncio
async def test_synchronous_success_outcome_is_delivered(full_settings):
    image = png_bytes()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded",
            image_bytes=image,
            image_count=1,
            evidence="result_available",
        )
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        done = await wait_status(client, created["id"], {"succeeded"})
        assert done["stage"] == "done"
        assert done["result_asset"]["kind"] == "generated"
        assert done["result_asset"]["byte_size"] == len(image)
        assert done["can_retrieve_result"] is False
        assert visual.poll_calls == []
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code", "expected_remote"),
    [
        (
            ProviderError("请求被服务端拒绝", evidence="rejected", error_code="VISUAL_REJECTED"),
            "failed",
            "VISUAL_REJECTED",
            None,
        ),
        (
            ProviderError("请求没有发出", evidence="not_submitted"),
            "failed",
            "VISUAL_NOT_SUBMITTED",
            None,
        ),
        (
            ProviderError(
                "请求可能已被接受", evidence="uncertain", remote_task_id="task-uncertain"
            ),
            "unknown",
            "VISUAL_OUTCOME_UNKNOWN",
            "task-uncertain",
        ),
    ],
)
async def test_submit_provider_error_evidence_controls_status(
    full_settings, error, expected_status, expected_code, expected_remote
):
    visual = VisualStub(submit_error=error)
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {expected_status})
        assert finished["stage"] == "submitting"
        assert finished["error"]["code"] == expected_code
        assert finished["remote_task_id"] == expected_remote
        assert finished["result_asset_id"] is None
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "unknown"])
async def test_result_available_evidence_is_a_retrievable_fetch_failure(
    full_settings, state
):
    locator = "https://cdn.example/ready.png?signature=private"
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state=state,
            evidence="result_available",
            remote_task_id="task-result-ready",
            result_locator=locator,
            result_expires_at="2099-01-01T00:00:00Z",
        )
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"failed"})
        assert finished["stage"] == "waiting_remote"
        assert finished["error"]["code"] == "RESULT_FETCH_FAILED"
        assert finished["remote_task_id"] == "task-result-ready"
        assert finished["result_asset_id"] is None
        assert finished["can_retrieve_result"] is True
        assert finished["retrieval_unavailable_reason"] is None
        assert visual.poll_calls == []
        assert len(visual.submit_calls) == 1

        row = generation_row(full_settings, created["id"])
        assert row[0] == "task-result-ready"
        assert json.loads(row[1]) == {
            "locator": locator,
            "expires_at": "2099-01-01T00:00:00Z",
        }


@pytest.mark.asyncio
async def test_unclassified_submit_exception_is_unknown_and_not_retried(full_settings):
    visual = VisualStub(submit_error=RuntimeError("connection reset while sending"))
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"unknown"})
        assert finished["error"]["code"] == "VISUAL_OUTCOME_UNKNOWN"
        assert finished["error"]["message"] == "生图结果未确认"
        assert finished["result_asset_id"] is None
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == []


@pytest.mark.asyncio
async def test_pending_without_remote_task_is_unknown_and_never_polls(full_settings):
    visual = VisualStub(submit_outcome=ProviderOutcome(state="pending"))
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"unknown"})
        assert finished["stage"] == "submitting"
        assert finished["error"]["code"] == "VISUAL_OUTCOME_UNKNOWN"
        assert finished["remote_task_id"] is None
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == []


@pytest.mark.asyncio
async def test_fetch_failure_is_safe_and_retains_private_locator(full_settings, caplog):
    locator = "https://cdn.example/result.png?signature=TOPSECRET"
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded",
            remote_task_id="task-fetch",
            result_locator=locator,
            result_expires_at="2026-10-30T00:00:00Z",
        ),
        fetch_error=RuntimeError(f"GET {locator} failed"),
    )
    caplog.set_level(logging.WARNING, logger="app.generations")
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"failed"})
        assert finished["stage"] == "fetching_result"
        assert finished["remote_task_id"] == "task-fetch"
        assert finished["error"]["code"] == "RESULT_FETCH_FAILED"
        assert finished["error"]["message"] == "生图结果下载失败"
        assert finished["result_asset_id"] is None
        assert "TOPSECRET" not in json.dumps(finished, ensure_ascii=False)
        assert visual.fetch_calls == [locator]
        assert len(visual.submit_calls) == 1

        row = generation_row(full_settings, created["id"])
        stored = json.loads(row[1])
        assert stored == {"locator": locator, "expires_at": "2026-10-30T00:00:00Z"}
        assert row[0] == "task-fetch"
        assert row[2] == "RESULT_FETCH_FAILED"
        assert "TOPSECRET" not in row[3]
        assert all("TOPSECRET" not in record.message for record in caplog.records)
        assert all("cdn.example" not in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_fetch_success_delivers_verified_result_and_keeps_locator(full_settings):
    locator = "https://cdn.example/result.png?signature=private"
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", remote_task_id="task-fetch-ok", result_locator=locator
        ),
        fetch_outcome=ProviderOutcome(
            state="succeeded",
            remote_task_id="task-fetch-ok",
            result_locator=locator,
            image_bytes=png_bytes(),
        ),
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        done = await wait_status(client, created["id"], {"succeeded"})
        assert done["stage"] == "done"
        assert done["result_asset_id"]
        assert visual.fetch_calls == [locator]
        assert len(visual.submit_calls) == 1
        row = generation_row(full_settings, created["id"])
        assert json.loads(row[1]) == locator


@pytest.mark.asyncio
async def test_poll_rejection_is_failed_without_further_polls(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="task-auth"),
        poll_errors=[
            ProviderError(
                "认证失败",
                evidence="rejected",
                error_code="VISUAL_AUTH_FAILED",
                remote_task_id="task-auth",
            )
        ],
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"failed"})
        assert finished["stage"] == "waiting_remote"
        assert finished["remote_task_id"] == "task-auth"
        assert finished["error"]["code"] == "VISUAL_AUTH_FAILED"
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == ["task-auth"]


@pytest.mark.asyncio
async def test_poll_unknown_outcome_is_unknown_and_retains_remote_identity(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="task-unknown"),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                evidence="uncertain",
                error_code="REMOTE_UNKNOWN",
                error_message="远程状态未知",
                remote_task_id="task-unknown",
            )
        ],
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"unknown"})
        assert finished["stage"] == "waiting_remote"
        assert finished["remote_task_id"] == "task-unknown"
        assert finished["error"]["code"] == "REMOTE_UNKNOWN"
        assert finished["error"]["message"] == "远程状态未知"
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "message_fragment"),
    [
        (ProviderOutcome(state="succeeded", extra={"images": [b"one", b"two"]}), "多张"),
        (ProviderOutcome(state="succeeded", image_bytes=b"<html>not an image</html>"), "无法解析"),
        (ProviderOutcome(state="succeeded", image_bytes=png_bytes(512, 512)), "尺寸"),
    ],
)
async def test_invalid_result_image_is_failed_without_asset(
    full_settings, outcome, message_fragment
):
    visual = VisualStub(submit_outcome=outcome)
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"failed"})
        assert finished["error"]["code"] == "RESULT_FETCH_FAILED"
        assert message_fragment in finished["error"]["message"]
        assert finished["result_asset_id"] is None
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_local_write_failure_is_failed_without_asset(full_settings, monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(generation_service, "write_asset_file", explode)
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes()))
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"failed"})
        assert finished["error"]["code"] == "RESULT_FETCH_FAILED"
        assert finished["error"]["message"] == "结果图片无法写入本机存储"
        assert finished["result_asset_id"] is None
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_poll_success_reuses_submit_locator_for_delivery(full_settings):
    locator = "https://cdn.example/result.png?signature=submit-locator"
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending", remote_task_id="task-locator", result_locator=locator
        ),
        poll_outcomes=[ProviderOutcome(state="succeeded", remote_task_id="task-locator")],
        fetch_outcome=ProviderOutcome(
            state="succeeded", remote_task_id="task-locator", image_bytes=png_bytes()
        ),
    )
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        done = await wait_status(client, created["id"], {"succeeded"})
        assert done["result_asset_id"]
        assert done["result_asset"]["kind"] == "generated"
        assert visual.fetch_calls == [locator]
        assert len(visual.submit_calls) == 1
        row = generation_row(full_settings, created["id"])
        assert row[0] == "task-locator"
        assert json.loads(row[1]) == locator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        ProviderOutcome(state="failed", evidence="not_submitted"),
        ProviderOutcome(state="pending", evidence="not_submitted"),
    ],
)
async def test_not_submitted_evidence_uses_not_submitted_code(full_settings, outcome):
    visual = VisualStub(submit_outcome=outcome)
    async with environment(full_settings, visual=visual) as (client, _registry, _llm, _visual):
        project = await create_confirmed_project(client)
        created = await submit(client, project)
        finished = await wait_status(client, created["id"], {"failed"})
        assert finished["stage"] == "submitting"
        assert finished["error"]["code"] == "VISUAL_NOT_SUBMITTED"
        assert finished["error"]["message"] == "生成任务未提交"
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == []
