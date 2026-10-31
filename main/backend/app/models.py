from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="未命名项目")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    brief_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    product_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    style: Mapped[str] = mapped_column(Text, nullable=False, default="")
    composition: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_size: Mapped[str | None] = mapped_column(String(32), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    reference_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    organize_operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    brief_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brief_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_projects_revision_non_negative"),
        CheckConstraint(
            "brief_status IN ('draft','organizing','needs_input','review','confirmed')",
            name="ck_projects_brief_status",
        ),
        CheckConstraint(
            "prompt_revision IS NULL OR prompt_revision <= revision",
            name="ck_projects_prompt_revision",
        ),
        CheckConstraint(
            "confirmed_revision IS NULL OR confirmed_revision <= revision",
            name="ck_projects_confirmed_revision",
        ),
        Index("ix_projects_updated_id", "updated_at", "id"),
        Index("ix_projects_owner_updated", "owner_account_id", "updated_at", "id"),
        Index("ix_projects_reference_asset", "reference_asset_id"),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    project_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    brief_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_messages_role"),
        CheckConstraint(
            "project_revision >= 0", name="ck_messages_project_revision_non_negative"
        ),
        UniqueConstraint("project_id", "sequence_no", name="uq_messages_project_sequence"),
        Index("ix_messages_project_sequence", "project_id", "sequence_no"),
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="quarantined", server_default="quarantined"
    )
    review_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('reference','generated')", name="ck_assets_kind"),
        CheckConstraint(
            "review_status IN ('quarantined','approved','rejected','legacy_unreviewed')",
            name="ck_assets_review_status",
        ),
        CheckConstraint("width > 0 AND height > 0", name="ck_assets_dimensions_positive"),
        CheckConstraint("byte_size >= 0", name="ck_assets_byte_size_non_negative"),
        Index("ix_assets_project_kind", "project_id", "kind"),
        Index("ix_assets_review_status", "review_status"),
    )


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    owner_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    operator_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    authorization_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)

    brief_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    image_size: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    adapter_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    service_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    remote_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_locator_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submit_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workflow_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "version_no", name="uq_generations_project_version"),
        UniqueConstraint("project_id", "request_id", name="uq_generations_project_request"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','unknown')",
            name="ck_generations_status",
        ),
        CheckConstraint("version_no >= 1", name="ck_generations_version_positive"),
        CheckConstraint(
            "confirmed_revision >= 0", name="ck_generations_confirmed_revision_non_negative"
        ),
        CheckConstraint(
            "stage IN ('waiting_slot','submitting','waiting_remote','fetching_result','done')",
            name="ck_generations_stage",
        ),
        Index(
            "uq_generations_active_project",
            "project_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running')"),
        ),
        Index("ix_generations_project_created", "project_id", "created_at"),
        Index("ix_generations_owner_created", "owner_account_id", "created_at"),
    )
