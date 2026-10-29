from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

OrganizeOutcomeKind = Literal["needs_input", "review"]
ProviderState = Literal["succeeded", "pending", "failed", "unknown"]
ProviderEvidence = Literal[
    "not_submitted", "rejected", "uncertain", "result_available"
]


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
class OrganizeImage:
    """One reference image supplied to a multimodal organizer."""

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class OrganizeRequest:
    brief: BriefData
    messages: tuple[OrganizeMessage, ...]
    user_text: str
    has_reference_image: bool
    reference_image: OrganizeImage | None = None


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
    poll_interval_seconds: float | None = None


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
    evidence: ProviderEvidence | None = None
    image_count: int | None = None
    result_expires_at: str | None = None


class ProviderError(Exception):
    """A provider failure carrying the evidence needed to classify its outcome.

    Adapters translate vendor responses and transport errors into this exception.
    ``message`` must be safe to show to a local user; credentials and signed URLs
    never belong in it.
    """

    def __init__(
        self,
        message: str,
        *,
        evidence: ProviderEvidence,
        error_code: str | None = None,
        remote_task_id: str | None = None,
        result_locator: str | None = None,
        result_expires_at: str | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = evidence
        self.error_code = error_code
        self.remote_task_id = remote_task_id
        self.result_locator = result_locator
        self.result_expires_at = result_expires_at


class ImageAdapter(Protocol):
    adapter_id: str
    model: str
    capabilities: ImageCapabilities

    async def submit(self, request: GenerationRequest) -> ProviderOutcome:
        """Submit exactly one generation request to the provider.

        A transport-level exception leaves acceptance uncertain; adapters must
        not retry the submission themselves. Return ``pending`` with a task id
        once acceptance is confirmed, or a terminal outcome when the provider
        already produced a result.
        """
        ...

    async def poll(self, remote_task_id: str) -> ProviderOutcome:
        """Read the current state of the original remote task.

        Polling must be read-only and use only the supplied task id. Return
        ``pending`` while the provider is still working. Provider errors must
        state their evidence so the service can distinguish a failed task from
        an unknown outcome; adapters must not submit a replacement task.
        """
        ...

    async def fetch_result(self, locator: str) -> ProviderOutcome:
        """Download the one result image behind a previously issued locator.

        This is the only download path the service uses. The adapter must
        enforce the provider-approved host and redirect policy hop by hop,
        must not send model authentication headers to CDN hosts, and must never
        follow a URL supplied by a client. The locator and signed URL stay
        private and must not appear in public DTOs or logs.
        """
        ...
