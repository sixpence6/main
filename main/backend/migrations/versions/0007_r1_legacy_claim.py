"""persist idempotent legacy project claims and rollback evidence

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "legacy_claims",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column(
            "admin_account_id",
            sa.String(length=36),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_root", sa.String(length=1024), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("backup_id", sa.String(length=64), nullable=False),
        sa.Column("backup_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("project_ids_json", sa.Text(), nullable=False),
        sa.Column("project_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_before_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_after_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="applied"),
        sa.CheckConstraint("project_count > 0", name="ck_legacy_claims_project_count"),
        sa.CheckConstraint("status IN ('applied')", name="ck_legacy_claims_status"),
        sa.UniqueConstraint("idempotency_key", name="uq_legacy_claims_idempotency_key"),
    )
    op.create_index(
        "ix_legacy_claims_admin_created",
        "legacy_claims",
        ["admin_account_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_legacy_claims_admin_created", table_name="legacy_claims")
    op.drop_table("legacy_claims")
