from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class IssueView(BaseModel):
    code: str
    message: str
    template_id: str | None = None
    params: dict[str, str] = Field(default_factory=dict)


class ArtifactView(BaseModel):
    id: str
    kind: str
    revision_no: int
    status: str
    digest: str
    payload: dict[str, Any] | None = None
    created_at: str


class ApprovalView(BaseModel):
    id: str
    approval_type: str
    artifact_id: str | None = None
    artifact_digest: str
    approved_by: str
    requirement_snapshot_digest: str
    referenced_material_digest: str
    prompt_version: str
    rules_version: str
    model_fingerprint: str
    checks: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    revoked_at: str | None = None
    revocation_reason: str | None = None


class ActionView(BaseModel):
    id: str
    label: str
    kind: Literal["button", "text", "confirm"] = "button"
    requires_text: bool = False
    text_label: str | None = None
    requires_confirmation: bool = False
    effect: Literal["read_only", "write", "model_call"] = "write"
    read_only: bool = False
    requires_model_call: bool = False
    retry_mode: Literal["none", "manual_only"] = "none"
    automatic_retry: bool = False


class LegacyItemView(BaseModel):
    id: str
    kind: str
    ref_id: str
    status: str
    note: str | None = None
    reviewed_at: str | None = None
    created_at: str


class GenerationJobView(BaseModel):
    id: str
    status: str
    request_id: str
    submit_attempted: bool
    result_asset_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class DeliveryView(BaseModel):
    id: str
    status: str
    asset_id: str
    filename: str
    caption: str | None = None
    published_at: str | None = None
    created_at: str


class WorkflowVersionView(BaseModel):
    run_id: str
    sequence_no: int
    requirement_revision: int
    status: str
    generation_job_id: str | None = None
    asset_id: str | None = None
    filename: str | None = None
    width: int | None = None
    height: int | None = None
    caption: str | None = None
    published_at: str | None = None
    brief: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    quality: dict[str, Any] | None = None
    execution_snapshot: dict[str, Any] | None = None
    execution_snapshot_digest: str | None = None
    snapshot_schema_version: str | None = None
    contract_version: str | None = None
    can_download: bool = False
    created_at: str


class GenerationUsageView(BaseModel):
    daily_limit: int
    used: int
    remaining: int
    window_started_at: str | None = None
    window_expires_at: str | None = None
    personal_active: int
    personal_limit: int
    instance_active: int
    instance_limit: int
    project_active: int
    project_limit: int


class GenerationIntentView(BaseModel):
    id: str
    request_id: str
    status: str
    artifact_digest: str
    requirement_revision: int
    created_at: str


class WorkflowProgressView(BaseModel):
    phase: Literal[
        "empty",
        "intake",
        "requirements",
        "prompt",
        "generation",
        "review",
        "delivery",
        "recovery",
    ] = "empty"
    stage: str | None = None
    stage_label: str | None = None
    status: Literal["none", "active", "needs_input", "blocked", "completed", "failed"] = "none"
    awaiting_user: bool = False
    terminal: bool = False


class ConstraintProjectionView(BaseModel):
    constraint_id: str
    path: str
    label: str
    strength: Literal["hard", "soft", "not_applicable"]
    source: str | None = None
    user_lock_basis: str | None = None
    rule_id: str | None = None
    value: Any = None


class TaskBindingView(BaseModel):
    project_id: str
    run_id: str | None = None
    project_revision: int
    requirement_revision: int
    intent_id: str | None = None
    intent_status: str | None = None
    intent_requirement_revision: int | None = None
    generation_job_id: str | None = None
    generation_job_status: str | None = None
    confirmed_snapshot_digest: str | None = None
    execution_snapshot_digest: str | None = None
    delivery_id: str | None = None
    delivery_status: str | None = None
    current: bool = True


class SafeReasonView(BaseModel):
    category: Literal[
        "missing_information",
        "repairable_composition",
        "true_drift",
        "content_rejection",
        "missing_evidence",
        "protocol_error",
        "technical_failure",
        "remote_unknown",
        "authorization_invalid",
    ]
    code: str
    message: str
    template_id: str | None = None
    params: dict[str, str] = Field(default_factory=dict)
    severity: Literal["needs_input", "blocked", "failed", "unknown"] = "blocked"
    requires_user_input: bool = False
    requires_new_version: bool = False
    requires_evidence: bool = False
    remote_unknown: bool = False
    auto_retry: bool = False


class ResourceStateView(BaseModel):
    current_run_id: str | None = None
    current_state: Literal[
        "none",
        "history_only",
        "pending",
        "published",
        "failed",
        "blocked",
        "unknown",
        "unavailable",
    ] = "none"
    current_version: WorkflowVersionView | None = None
    history: list[WorkflowVersionView] = Field(default_factory=list)


class TaskStateView(BaseModel):
    progress: WorkflowProgressView
    reason: SafeReasonView | None = None
    binding: TaskBindingView
    constraints: list[ConstraintProjectionView] = Field(default_factory=list)
    resource_state: ResourceStateView


class WorkflowView(BaseModel):
    project_id: str
    run_id: str | None = None
    stage: str | None = None
    status: Literal["none", "active", "needs_input", "blocked", "completed", "failed"] = "none"
    project_revision: int
    requirement_revision: int
    intent_route: str | None = None
    intent_action: str | None = None
    issue: IssueView | None = None
    artifacts: list[ArtifactView] = Field(default_factory=list)
    approvals: list[ApprovalView] = Field(default_factory=list)
    available_actions: list[ActionView] = Field(default_factory=list)
    legacy: list[LegacyItemView] = Field(default_factory=list)
    generation_job: GenerationJobView | None = None
    delivery: DeliveryView | None = None
    versions: list[WorkflowVersionView] = Field(default_factory=list)
    execution_snapshot: dict[str, Any] | None = None
    execution_snapshot_digest: str | None = None
    usage: GenerationUsageView | None = None
    intent: GenerationIntentView | None = None
    task_state: TaskStateView | None = None
    updated_at: str | None = None


class WorkflowActionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    text: str | None = Field(default=None, max_length=20000)
    payload: dict[str, Any] | None = None

    model_config = {"extra": "forbid"}
