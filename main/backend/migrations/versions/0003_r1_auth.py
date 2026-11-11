"""R1 auth foundation

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email_normalized", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("password_set_at", sa.String(length=32), nullable=True),
        sa.Column("password_policy_version", sa.String(length=32), nullable=True),
        sa.Column("is_admin", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_disabled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disabled_at", sa.String(length=32), nullable=True),
        sa.Column("disabled_by", sa.String(length=36), nullable=True),
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_login_at", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint("length(email_normalized) BETWEEN 3 AND 254", name="ck_accounts_email_length"),
        sa.CheckConstraint(
            "password_hash IS NULL OR length(password_hash) BETWEEN 20 AND 512",
            name="ck_accounts_password_hash_length",
        ),
        sa.CheckConstraint("is_admin IN (0, 1)", name="ck_accounts_is_admin"),
        sa.CheckConstraint("is_disabled IN (0, 1)", name="ck_accounts_is_disabled"),
        sa.CheckConstraint("session_epoch >= 0", name="ck_accounts_session_epoch"),
        sa.CheckConstraint(
            "(password_hash IS NULL AND password_set_at IS NULL) OR "
            "(password_hash IS NOT NULL AND password_set_at IS NOT NULL)",
            name="ck_accounts_password_state",
        ),
        sa.CheckConstraint(
            "(is_disabled = 0 AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(is_disabled = 1 AND disabled_at IS NOT NULL)",
            name="ck_accounts_disabled_state",
        ),
        sa.ForeignKeyConstraint(["disabled_by"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_accounts_email_normalized", "accounts", ["email_normalized"], unique=True)
    op.create_index(
        "uq_accounts_single_admin",
        "accounts",
        ["is_admin"],
        unique=True,
        sqlite_where=sa.text("is_admin = 1"),
    )
    op.create_index("ix_accounts_disabled_created", "accounts", ["is_disabled", "created_at"])

    op.create_table(
        "instance_bootstrap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_account_id", sa.String(length=36), nullable=False),
        sa.Column("initialized_at", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_instance_bootstrap_singleton"),
        sa.CheckConstraint("method IN ('local_cli')", name="ck_instance_bootstrap_method"),
        sa.ForeignKeyConstraint(["admin_account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("admin_account_id", name="uq_instance_bootstrap_admin"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("account_epoch", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("last_activity_at", sa.String(length=32), nullable=False),
        sa.Column("idle_expires_at", sa.String(length=32), nullable=False),
        sa.Column("absolute_expires_at", sa.String(length=32), nullable=False),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
        sa.Column("revoked_reason", sa.String(length=64), nullable=True),
        sa.Column("client_info", sa.String(length=200), nullable=True),
        sa.Column("source_ref", sa.String(length=64), nullable=True),
        sa.CheckConstraint("account_epoch >= 0", name="ck_sessions_account_epoch"),
        sa.CheckConstraint(
            "absolute_expires_at > created_at", name="ck_sessions_absolute_after_created"
        ),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at", name="ck_sessions_idle_within_absolute"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_reason IS NOT NULL", name="ck_sessions_revocation_reason"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_sessions_token_digest", "sessions", ["token_digest"], unique=True)
    op.create_index("ix_sessions_account_active", "sessions", ["account_id", "revoked_at"])
    op.create_index("ix_sessions_absolute_expiry", "sessions", ["absolute_expires_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_kind", sa.String(length=24), nullable=False),
        sa.Column("actor_account_id", sa.String(length=36), nullable=True),
        sa.Column("target_account_id", sa.String(length=36), nullable=True),
        sa.Column("target_kind", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("subject_ref", sa.String(length=64), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=True),
        sa.Column("before_json", sa.Text(), nullable=True),
        sa.Column("after_json", sa.Text(), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "actor_kind IN ('account','admin','anonymous','system','local_maintainer')",
            name="ck_audit_events_actor_kind",
        ),
        sa.CheckConstraint(
            "result IN ('success','failure','denied','limited')",
            name="ck_audit_events_result",
        ),
        sa.CheckConstraint(
            "detail_json IS NULL OR length(detail_json) <= 4096",
            name="ck_audit_events_detail_size",
        ),
        sa.ForeignKeyConstraint(["actor_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_occurred", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_actor", "audit_events", ["actor_account_id", "occurred_at"])
    op.create_index("ix_audit_target", "audit_events", ["target_account_id", "occurred_at"])
    op.create_index("ix_audit_type", "audit_events", ["event_type", "occurred_at"])
    op.create_index("ix_audit_subject", "audit_events", ["subject_ref", "occurred_at"])

    op.create_table(
        "rate_limit_facts",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("bucket_class", sa.String(length=32), nullable=False),
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="pending"),
        sa.CheckConstraint(
            "bucket_class IN ('login_account','login_source','link_issue_admin',"
            "'link_issue_target','link_verify_credential','link_verify_source')",
            name="ck_rate_limit_facts_class",
        ),
        sa.CheckConstraint("weight > 0", name="ck_rate_limit_facts_weight"),
        sa.CheckConstraint(
            "outcome IN ('pending','failure')", name="ck_rate_limit_facts_outcome"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rate_class_key_time", "rate_limit_facts", ["bucket_class", "bucket_key", "occurred_at"]
    )
    op.create_index("ix_rate_expiry", "rate_limit_facts", ["expires_at"])
    op.create_index("ix_rate_class_key", "rate_limit_facts", ["bucket_class", "bucket_key"])


def downgrade() -> None:
    op.drop_table("rate_limit_facts")
    op.drop_table("audit_events")
    op.drop_table("sessions")
    op.drop_table("instance_bootstrap")
    op.drop_table("accounts")
