from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

from PIL import Image

from app.adapters.base import (
    BriefData,
    GenerationRequest,
    ImageCapabilities,
    OrganizeRequest,
    OrganizeResult,
    ProviderOutcome,
)
from app.adapters.registry import AdapterRegistry
from app.config import Settings
from app.main import create_app


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1024, 1024), (240, 240, 240)).save(output, format="PNG")
    return output.getvalue()


_PNG_BYTES = _png_bytes()


def _log(event: str) -> None:
    path = os.environ.get("T10_CALL_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(event + "\n")


class ScriptedLLM:
    adapter_id = "stub-llm"

    async def organize(self, request: OrganizeRequest) -> OrganizeResult:
        if "BLOCK_ORGANIZE" in request.user_text:
            _log("llm:block")
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        _log("llm:review")
        return OrganizeResult(
            outcome="review",
            assistant_message="需求已整理完成，请确认。",
            brief=BriefData(
                product_description="白色陶瓷马克杯，容量 350ml",
                purpose="电商主图",
                style="干净明亮",
                composition="居中构图",
                text_content="限时 9 折",
                image_size=request.brief.image_size,
                reference_asset_id=request.brief.reference_asset_id,
                prompt="一只白色陶瓷马克杯放在浅色木桌上，柔和自然光，居中构图，电商主图",
            ),
        )


class ScriptedVisual:
    adapter_id = "stub-visual"
    model = "stub-model"
    capabilities = ImageCapabilities(
        text_to_image=True,
        reference_image=True,
        asynchronous=True,
        supported_image_sizes=("1024x1024",),
        reference_mime_types=("image/png", "image/jpeg"),
        provider_max_reference_bytes=5_000_000,
        poll_interval_seconds=0.01,
    )

    async def submit(self, request: GenerationRequest) -> ProviderOutcome:
        marker = os.environ.get("T10_BLOCK_MARKER")
        blocked = bool(marker) and await asyncio.to_thread(Path(marker).exists)
        _log(f"submit:{request.project_id}:{'blocked' if blocked else 'success'}")
        if blocked:
            return ProviderOutcome(
                state="pending",
                remote_task_id=f"remote-{request.project_id[:8]}",
                result_locator="https://cdn.invalid/private-t10.png",
            )
        return ProviderOutcome(state="succeeded", image_bytes=_PNG_BYTES)

    async def poll(self, remote_task_id: str) -> ProviderOutcome:
        _log(f"poll:{remote_task_id}")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def fetch_result(self, locator: str) -> ProviderOutcome:
        _log(f"fetch:{locator}")
        return ProviderOutcome(state="succeeded", image_bytes=_PNG_BYTES)


def build_app():
    registry = AdapterRegistry()
    registry.register_llm("stub-llm", lambda role, client: ScriptedLLM())
    visual = ScriptedVisual()
    registry.register_visual(
        visual.adapter_id,
        lambda role, client: visual,
        capabilities=visual.capabilities,
    )
    return create_app(settings=Settings(), registry=registry)


app = build_app()
