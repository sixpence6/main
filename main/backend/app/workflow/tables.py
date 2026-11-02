from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(8), nullable=False, default="S01")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    intent_route: Mapped[str | None] = mapped_column(String(16), nullable=True)
    intent_action: Mapped[str | None] = mapped_column(String(24), nullable=True)
    requirement_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issue_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issue_message: Mapped[str | None] = mapped_column(String(800), nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    template_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_claim_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_claimed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "sequence_no", name="uq_workflow_runs_project_sequence"),
        CheckConstraint("status IN ('active','needs_input','blocked','completed','failed')", name="ck_workflow_runs_status"),
        Index("ix_workflow_runs_project_created", "project_id", "created_at"),
    )


class RoleCall(Base):
    __tablename__ = "role_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    stage: Mapped[str] = mapped_column(String(8), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    capability: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_fingerprint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("capability IN ('TEXT','VISION','IMAGE_GEN')", name="ck_role_calls_capability"),
        CheckConstraint("status IN ('ok','invalid','error','timeout')", name="ck_role_calls_status"),
        Index("ix_role_calls_run_created", "run_id", "started_at"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "kind", "revision_no", name="uq_artifacts_run_kind_revision"),
        CheckConstraint("status IN ('candidate','approved','rejected','superseded')", name="ck_artifacts_status"),
        Index("ix_artifacts_run_kind", "run_id", "kind"),
    )


class ArtifactReview(Base):
    __tablename__ = "artifact_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(40), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    checks_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("target_kind IN ('artifact','image')", name="ck_artifact_reviews_target_kind"),
        CheckConstraint("decision IN ('pass','revise','reject','unknown')", name="ck_artifact_reviews_decision"),
        Index("ix_artifact_reviews_artifact", "artifact_id", "created_at"),
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    approval_type: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    referenced_material_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    checks_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("approved_by IN ('user','output_reviewer','quality_compliance')", name="ck_approvals_approved_by"),
        Index("ix_approvals_project_type_revoked", "project_id", "approval_type", "revoked_at"),
    )


class RevisionBudget(Base):
    __tablename__ = "revision_budgets"

    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), primary_key=True)
    artifact_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_allowed: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    __table_args__ = (
        CheckConstraint("used >= 0 AND used <= max_allowed", name="ck_revision_budgets_used"),
        CheckConstraint("max_allowed = 3", name="ck_revision_budgets_max"),
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False)
    generation_id: Mapped[str | None] = mapped_column(ForeignKey("generations.id", ondelete="SET NULL"), nullable=True)
    owner_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    operator_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    authorization_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("generation_intents.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    confirmed_snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    submit_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remote_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('created','submit_pending','submitted','remote_pending','result_fetched','failed','unknown','quarantined','approved','rejected','published')", name="ck_generation_jobs_status"),
        Index("ix_generation_jobs_project_created", "project_id", "created_at"),
        Index("ix_generation_jobs_owner_created", "owner_account_id", "created_at"),
        Index("ix_generation_jobs_intent", "intent_id"),
    )


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    generation_job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    manifest_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approved")
    filename: Mapped[str] = mapped_column(String(120), nullable=False)
    caption: Mapped[str] = mapped_column(String(800), nullable=False)
    asset_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_deliveries_asset"),
        CheckConstraint("status IN ('approved','published','revoked')", name="ck_deliveries_status"),
        Index("ix_deliveries_project_status", "project_id", "status"),
    )


class LegacyItem(Base):
    __tablename__ = "legacy_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy_unreviewed")
    approved_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(String(800), nullable=True)
    reviewed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uq_legacy_items_kind_ref"),
        CheckConstraint("kind IN ('message','generation','asset')", name="ck_legacy_items_kind"),
        CheckConstraint("status IN ('legacy_unreviewed','approved')", name="ck_legacy_items_status"),
        Index("ix_legacy_items_project", "project_id", "status"),
    )


class DiagnosticEvent(Base):
    __tablename__ = "diagnostic_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_diagnostic_events_created", "created_at"),)
