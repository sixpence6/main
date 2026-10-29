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
from tests.support.adapters import LLMStub


def _result_image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1024, 1024), (244, 244, 244)).save(buffer, format="PNG")
    return buffer.getvalue()


RESULT_IMAGE = _result_image()


def _review(_request: OrganizeRequest) -> OrganizeResult:
    return OrganizeResult(
        outcome="review",
        assistant_message="需求已整理，请确认",
        brief=BriefData(
            product_description="白色陶瓷马克杯，容量 350ml",
            purpose="电商主图",
            style="干净明亮",
            composition="商品居中，留出文案空间",
            text_content="",
            prompt="白色陶瓷马克杯电商主图，干净明亮，商品居中",
        ),
    )


class TrackingVisual:
    """Test-only synchronous image adapter that records serialized submissions."""

    adapter_id = "stub-visual"
    model = "stub-model"
    capabilities = ImageCapabilities(
        text_to_image=True,
        reference_image=True,
        asynchronous=True,
        supported_image_sizes=("1024x1024",),
        reference_mime_types=("image/png", "image/jpeg"),
        provider_max_reference_bytes=5_000_000,
    )

    def __init__(self) -> None:
        self.marker = Path(os.environ.get("VISUAL_CALL_MARKER", "visual-calls.log"))
        self.delay_seconds = float(os.environ.get("VISUAL_DELAY_SECONDS", "2"))

    def _append(self, line: str) -> None:
        with self.marker.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    async def submit(self, request: GenerationRequest) -> ProviderOutcome:
        await asyncio.to_thread(self._append, f"start {request.project_id}")
        try:
            await asyncio.sleep(self.delay_seconds)
            return ProviderOutcome(state="succeeded", image_bytes=RESULT_IMAGE)
        finally:
            await asyncio.to_thread(self._append, f"end {request.project_id}")

    async def poll(self, _remote_task_id: str) -> ProviderOutcome:
        raise AssertionError("synchronous test adapter never polls")

    async def fetch_result(self, _locator: str) -> ProviderOutcome:
        raise AssertionError("synchronous test adapter never fetches")


def build_app():
    registry = AdapterRegistry()
    llm = LLMStub(responder=_review)
    visual = TrackingVisual()
    registry.register_llm(llm.adapter_id, lambda role, client: llm)
    registry.register_visual(
        visual.adapter_id,
        lambda role, client: visual,
        capabilities=visual.capabilities,
    )
    return create_app(settings=Settings(), registry=registry)


app = build_app()
