from __future__ import annotations

from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

BriefStatus = Literal["draft", "organizing", "needs_input", "review", "confirmed"]
GenerationStatus = Literal["queued", "running", "succeeded", "failed", "unknown"]
GenerationStage = Literal["waiting_slot", "submitting", "waiting_remote", "fetching_result", "done"]


class Brief(BaseModel):
    product_description: str = ""
    purpose: str = ""
    style: str = ""
    composition: str = ""
    text_content: str = ""
    image_size: str | None = None
    reference_asset_id: str | None = None
    prompt: str = ""


class AssetView(BaseModel):
    id: str
    kind: Literal["reference", "generated"]
    mime_type: str
    width: int
    height: int
    byte_size: int
    created_at: str
    availability: Literal["available", "missing"]


class ErrorView(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """Public error envelope returned by every API exception."""

    error: ErrorView


ERROR_RESPONSE = {"model": ErrorResponse, "description": "公开错误"}


class ProjectView(BaseModel):
    id: str
    title: str
    revision: int
    prompt_revision: int | None
    confirmed_revision: int | None
    brief_status: BriefStatus
    brief: Brief
    reference_asset: AssetView | None
    brief_error: ErrorView | None
    created_at: str
    updated_at: str


class MessageView(BaseModel):
    id: str
    sequence_no: int
    role: Literal["user", "assistant", "system"]
    content: str
    project_revision: int
    brief_snapshot: Brief | None
    created_at: str


class GenerationView(BaseModel):
    id: str
    project_id: str
    version_no: int
    request_id: str
    confirmed_revision: int
    status: GenerationStatus
    stage: GenerationStage
    brief_snapshot: Brief | None
    prompt: str
    reference_asset_id: str | None
    adapter_id: str
    model: str
    remote_task_id: str | None
    result_asset_id: str | None
    result_asset: AssetView | None
    error: ErrorView | None
    can_retrieve_result: bool
    retrieval_unavailable_reason: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


class ProjectSummary(BaseModel):
    id: str
    title: str | None
    brief_status: BriefStatus
    latest_generation_status: GenerationStatus | None
    updated_at: str


class ProjectListResponse(BaseModel):
    items: list[ProjectSummary]
    next_cursor: str | None


class ProjectDetailResponse(BaseModel):
    project: ProjectView
    messages: list[MessageView]
    generations: list[GenerationView]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ConfigIssue(BaseModel):
    code: str
    message: str


class LLMConfigStatus(BaseModel):
    configured: bool
    ready: bool
    adapter_id: str | None
    model: str | None
    missing_fields: list[str]
    issues: list[ConfigIssue]
    timeout_seconds: int
    verification: Literal["not_checked"]


class VisualCapabilities(BaseModel):
    text_to_image: bool | None
    reference_image: bool | None
    asynchronous: bool | None
    supported_image_sizes: list[str] | None
    selected_image_size: str | None
    reference_mime_types: list[str] | None
    provider_max_reference_bytes: int | None
    effective_reference_max_bytes: int | None
    # T02：本地限制与最终生效限制分开表达，前端不需要自行推断“更严格者”。
    local_reference_max_bytes: int | None
    effective_reference_mime_types: list[str] | None
    usable_image_size: str | None


class VisualConfigStatus(LLMConfigStatus):
    capabilities: VisualCapabilities


class CapabilityStatus(BaseModel):
    # T02：TEXT/VISION/IMAGE_GEN 三种能力各自独立表达，禁止把生图配置当成视觉理解配置。
    capability: Literal["TEXT", "VISION", "IMAGE_GEN"]
    configured: bool
    ready: bool
    adapter_id: str | None
    model: str | None
    missing_fields: list[str]
    issues: list[ConfigIssue]
    timeout_seconds: int
    verification: Literal["not_checked"]
    model_fingerprint: str | None
    declared_capabilities: list[str]


class VisionConfigStatus(CapabilityStatus):
    endpoint_source: Literal["shared_text", "independent", "missing"]
    credential_source: Literal["shared_text", "independent", "missing"]


class ImageGenerationConfigStatus(CapabilityStatus):
    capabilities: VisualCapabilities


class GenerationGate(BaseModel):
    allowed: bool
    missing_capabilities: list[str]
    issues: list[ConfigIssue]
    verification: Literal["not_checked"]


class ConfigStatus(BaseModel):
    llm: LLMConfigStatus
    visual: VisualConfigStatus
    text: CapabilityStatus
    vision: VisionConfigStatus
    image_generation: ImageGenerationConfigStatus
    generation: GenerationGate


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(EmptyBody):
    pass


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)
    expected_revision: StrictInt = Field(ge=0)


class UpdateBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: StrictInt = Field(ge=0)
    product_description: str | None = Field(default=None, max_length=8_000)
    purpose: str | None = Field(default=None, max_length=8_000)
    style: str | None = Field(default=None, max_length=8_000)
    composition: str | None = Field(default=None, max_length=8_000)
    text_content: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateBriefRequest:
        names = (
            "product_description",
            "purpose",
            "style",
            "composition",
            "text_content",
        )
        for name in names:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} 不能为 null")
        if not any(getattr(self, name) is not None for name in names):
            raise ValueError("至少提供一个可编辑字段")
        return self


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: StrictInt = Field(ge=0)


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID4
    confirmed_revision: StrictInt = Field(ge=0)
    acknowledge_unknown: StrictBool | None = None


class MessageExchangeResponse(BaseModel):
    project: ProjectView
    user_message: MessageView
    assistant_message: MessageView


class GenerationResponse(BaseModel):
    generation: GenerationView
