from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

OrganizeOutcomeKind = Literal["needs_input", "review"]
ProviderState = Literal["succeeded", "pending", "failed", "unknown"]


@dataclass(frozen=True)
class BriefData:
    product_description: str = ""
    purpose: str = ""
    style: str = ""
    composition: str = ""
    text_content: str = ""
    image_size: str | None = None
    reference_asset_id: str | None = None
    prompt: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "product_description": self.product_description,
            "purpose": self.purpose,
            "style": self.style,
            "composition": self.composition,
            "text_content": self.text_content,
            "image_size": self.image_size,
            "reference_asset_id": self.reference_asset_id,
            "prompt": self.prompt,
        }


@dataclass(frozen=True)
class OrganizeMessage:
    role: str
    content: str


@dataclass(frozen=True)
class OrganizeRequest:
    brief: BriefData
    messages: tuple[OrganizeMessage, ...]
    user_text: str
    has_reference_image: bool


@dataclass(frozen=True)
class OrganizeResult:
    outcome: OrganizeOutcomeKind
    assistant_message: str
    brief: BriefData


class LLMAdapter(Protocol):
    adapter_id: str

    async def organize(self, request: OrganizeRequest) -> OrganizeResult: ...


@dataclass(frozen=True)
class ImageCapabilities:
    text_to_image: bool | None = None
    reference_image: bool | None = None
    asynchronous: bool | None = None
    supported_image_sizes: tuple[str, ...] | None = None
    reference_mime_types: tuple[str, ...] | None = None
    provider_max_reference_bytes: int | None = None


@dataclass(frozen=True)
class GenerationRequest:
    project_id: str
    version_no: int
    prompt: str
    brief: BriefData
    image_size: str | None
    reference_path: Path | None = None
    reference_mime_type: str | None = None


@dataclass(frozen=True)
class ProviderOutcome:
    state: ProviderState
    remote_task_id: str | None = None
    result_locator: str | None = None
    image_bytes: bytes | None = None
    error_code: str | None = None
    error_message: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


class ImageAdapter(Protocol):
    adapter_id: str
    model: str
    capabilities: ImageCapabilities

    async def submit(self, request: GenerationRequest) -> ProviderOutcome: ...

    async def poll(self, remote_task_id: str) -> ProviderOutcome: ...

    async def fetch_result(self, locator: str) -> ProviderOutcome: ...
