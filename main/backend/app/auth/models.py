from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email_normalized: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_set_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    password_policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_admin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_disabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disabled_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    disabled_by: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_login_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "length(email_normalized) BETWEEN 3 AND 254", name="ck_accounts_email_length"
        ),
        CheckConstraint("is_admin IN (0, 1)", name="ck_accounts_is_admin"),
        CheckConstraint("is_disabled IN (0, 1)", name="ck_accounts_is_disabled"),
        CheckConstraint("session_epoch >= 0", name="ck_accounts_session_epoch"),
        Index("uq_accounts_email_normalized", "email_normalized", unique=True),
        Index(
            "uq_accounts_single_admin",
            "is_admin",
            unique=True,
            sqlite_where=text("is_admin = 1"),
        ),
        Index("ix_accounts_disabled_created", "is_disabled", "created_at"),
    )

    @property
    def status(self) -> str:
        if self.is_disabled:
            return "disabled"
        if not self.password_hash:
            return "pending_setup"
        return "active"

    @property
    def can_login(self) -> bool:
        return self.status == "active"


class InstanceBootstrap(Base):
    __tablename__ = "instance_bootstrap"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    initialized_at: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_instance_bootstrap_singleton"),
        CheckConstraint("method IN ('local_cli')", name="ck_instance_bootstrap_method"),
    )


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    account_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    last_activity_at: Mapped[str] = mapped_column(String(32), nullable=False)
    idle_expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    absolute_expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_info: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("account_epoch >= 0", name="ck_sessions_account_epoch"),
        CheckConstraint(
            "absolute_expires_at > created_at", name="ck_sessions_absolute_after_created"
        ),
        CheckConstraint(
            "idle_expires_at <= absolute_expires_at", name="ck_sessions_idle_within_absolute"
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_reason IS NOT NULL", name="ck_sessions_revocation_reason"
        ),
        Index("ix_sessions_account_active", "account_id", "revoked_at"),
        Index("ix_sessions_absolute_expiry", "absolute_expires_at"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurred_at: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    target_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    target_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "actor_kind IN ('account','admin','anonymous','system','local_maintainer')",
            name="ck_audit_events_actor_kind",
        ),
        CheckConstraint(
            "result IN ('success','failure','denied','limited')",
            name="ck_audit_events_result",
        ),
        CheckConstraint(
            "detail_json IS NULL OR length(detail_json) <= 4096",
            name="ck_audit_events_detail_size",
        ),
        Index("ix_audit_occurred", "occurred_at"),
        Index("ix_audit_actor", "actor_account_id", "occurred_at"),
        Index("ix_audit_target", "target_account_id", "occurred_at"),
        Index("ix_audit_type", "event_type", "occurred_at"),
        Index("ix_audit_subject", "subject_ref", "occurred_at"),
    )


class RateLimitFact(Base):
    __tablename__ = "rate_limit_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket_class: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket_key: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    __table_args__ = (
        CheckConstraint(
            "bucket_class IN ('login_account','login_source','link_issue_admin',"
            "'link_issue_target','link_verify_credential','link_verify_source')",
            name="ck_rate_limit_facts_class",
        ),
        CheckConstraint("weight > 0", name="ck_rate_limit_facts_weight"),
        CheckConstraint(
            "outcome IN ('pending','failure')", name="ck_rate_limit_facts_outcome"
        ),
        Index("ix_rate_class_key_time", "bucket_class", "bucket_key", "occurred_at"),
        Index("ix_rate_expiry", "expires_at"),
        Index("ix_rate_class_key", "bucket_class", "bucket_key"),
    )
