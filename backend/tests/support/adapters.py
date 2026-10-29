from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from app.adapters.base import (
    BriefData,
    GenerationRequest,
    ImageCapabilities,
    OrganizeRequest,
    OrganizeResult,
    ProviderOutcome,
)
from app.adapters.registry import AdapterRegistry
from app.config import RoleConfig


@dataclass
class LLMStub:
    """Scripted LLM adapter. Lives in tests only; production registry never builds it."""

    adapter_id: str = "stub-llm"
    responder: Callable[[OrganizeRequest], OrganizeResult] | None = None
    calls: list[OrganizeRequest] = field(default_factory=list)
    delay_seconds: float = 0.0
    gate: asyncio.Event | None = None

    async def organize(self, request: OrganizeRequest) -> OrganizeResult:
        self.calls.append(request)
        if self.gate is not None:
            await self.gate.wait()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.responder is None:
            return OrganizeResult(
                outcome="needs_input",
                assistant_message="请补充商品信息",
                brief=request.brief,
            )
        return self.responder(request)


@dataclass
class VisualStub:
    """Scripted image adapter with explicit control over every provider stage."""

    adapter_id: str = "stub-visual"
    model: str = "stub-model"
    capabilities: ImageCapabilities = field(
        default_factory=lambda: ImageCapabilities(
            text_to_image=True,
            reference_image=True,
            asynchronous=True,
            supported_image_sizes=("1024x1024",),
            reference_mime_types=("image/png", "image/jpeg"),
            provider_max_reference_bytes=5_000_000,
        )
    )
    submit_outcome: ProviderOutcome | None = None
    poll_outcomes: list[ProviderOutcome] = field(default_factory=list)
    fetch_outcome: ProviderOutcome | None = None
    submit_calls: list[GenerationRequest] = field(default_factory=list)
    poll_calls: list[str] = field(default_factory=list)
    fetch_calls: list[str] = field(default_factory=list)
    fail_after_submit: bool = False
    gate: asyncio.Event | None = None
    delay_seconds: float = 0.0
    active_count: int = 0
    max_active_count: int = 0

    async def submit(self, request: GenerationRequest) -> ProviderOutcome:
        self.submit_calls.append(request)
        self.active_count += 1
        self.max_active_count = max(self.max_active_count, self.active_count)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if self.submit_outcome is None:
                raise AssertionError("submit_outcome must be configured by the test")
            return self.submit_outcome
        finally:
            self.active_count -= 1

    async def poll(self, remote_task_id: str) -> ProviderOutcome:
        self.poll_calls.append(remote_task_id)
        if not self.poll_outcomes:
            return ProviderOutcome(state="pending")
        return self.poll_outcomes.pop(0) if len(self.poll_outcomes) > 1 else self.poll_outcomes[0]

    async def fetch_result(self, locator: str) -> ProviderOutcome:
        self.fetch_calls.append(locator)
        if self.fetch_outcome is None:
            raise AssertionError("fetch_outcome must be configured by the test")
        return self.fetch_outcome


@dataclass
class Recorder:
    llm_builds: int = 0
    visual_builds: int = 0


def build_test_registry(
    llm: LLMStub | None = None,
    visual: VisualStub | None = None,
    recorder: Recorder | None = None,
) -> AdapterRegistry:
    registry = AdapterRegistry()
    recorder = recorder or Recorder()
    llm = llm or LLMStub()
    visual = visual or VisualStub()

    def build_llm(role: RoleConfig, client: httpx.AsyncClient) -> LLMStub:
        recorder.llm_builds += 1
        return llm

    def build_visual(role: RoleConfig, client: httpx.AsyncClient) -> VisualStub:
        recorder.visual_builds += 1
        return visual

    registry.register_llm(llm.adapter_id, build_llm)
    registry.register_visual(visual.adapter_id, build_visual, capabilities=visual.capabilities)
    registry.stub_llm = llm  # type: ignore[attr-defined]
    registry.stub_visual = visual  # type: ignore[attr-defined]
    registry.recorder = recorder  # type: ignore[attr-defined]
    return registry


def default_brief() -> BriefData:
    return BriefData()
